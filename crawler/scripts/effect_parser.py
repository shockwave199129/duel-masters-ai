"""Parse scraped Duel Masters card abilities into structured DB rows.

Main flow:
1. Read cards whose URL status is ``scraped``.
2. Extract each ``■`` ability from the stored raw card text.
3. Ask OpenRouter to convert those abilities into structured JSON.
4. Save the JSON into ``card_effects`` and mark the card URL as ``parsed``.

The public entry point is ``parse_pending_cards()``.
"""

from __future__ import annotations
import json
import logging
import time
import random
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as futures_wait
from importlib import import_module
from typing import Any
from typing import Callable, Optional

import openai
import psycopg2
import psycopg2.extras
from openai import OpenAI
from openrouter import OpenRouter
from openrouter import errors as openrouter_errors
from pydantic import BaseModel

from scripts.rules_context import RulesContextConfig, build_rules_context

logger = logging.getLogger(__name__)

EMPTY_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
LLM_PROVIDERS = ("openrouter", "openai", "ollama", "local-hf", "nvidia")
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_LOCAL_HF_TIMEOUT = 3600.0


class _NullLlmClient:
    """Context manager placeholder for providers that do not need an SDK client."""

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False


class _ClientContext:
    """Tiny context manager for clients that do not provide one."""

    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, exc_type, exc, traceback):
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        return False


class _LocalHfClient:
    """Load a local PEFT/LoRA adapter and generate chat responses."""

    def __init__(self, adapter_path: str, generation_timeout: float = DEFAULT_LOCAL_HF_TIMEOUT):
        self.adapter_path = adapter_path
        self.generation_timeout = generation_timeout
        self.tokenizer = None
        self.model = None
        self.torch = None
        self.device = "cpu"

    def __enter__(self):
        self.torch = import_module("torch")
        transformers = import_module("transformers")
        peft = import_module("peft")

        self.device = "cuda" if self.torch.cuda.is_available() else "cpu"
        dtype = self.torch.float16 if self.torch.cuda.is_available() else self.torch.float32

        peft_config = peft.PeftConfig.from_pretrained(self.adapter_path)
        base_model = peft_config.base_model_name_or_path
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(self.adapter_path)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        model = transformers.AutoModelForCausalLM.from_pretrained(
            base_model,
            dtype=dtype,
            low_cpu_mem_usage=False,
        )
        self.model = peft.PeftModel.from_pretrained(model, self.adapter_path, is_trainable=False)
        self.model = self.model.to(self.device)
        self.model.eval()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.model = None
        self.tokenizer = None
        if self.torch and self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()
        return False

    def chat(self, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, dict[str, int]]:
        if self.model is None or self.tokenizer is None or self.torch is None:
            raise RuntimeError("Local HF client is not loaded")

        if hasattr(self.tokenizer, "apply_chat_template"):
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            prompt = "\n".join(f"{message['role']}: {message['content']}" for message in messages)
            prompt += "\nassistant:"

        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        prompt_tokens = int(inputs["input_ids"].shape[-1])
        with self.torch.no_grad():
            output = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                max_time=self.generation_timeout,
                do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        generated = output[0][prompt_tokens:]
        text = self.tokenizer.decode(generated, skip_special_tokens=True)
        completion_tokens = int(generated.shape[-1])
        return text, {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

# ── Prompt ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert Duel Masters card game rules engine parser.
Given a card's raw ability text lines (each starting with ■), you output a JSON object
with an "effects" array where each element is the structured representation of one ability.

Each effect object must have these exact fields:
{
  "ability_index": <int, 0-based position in the list>,
  "raw_text": "<exact input line>",
  "effect_type": "<one of: keyword|triggered|activated|static|replacement|cost_mod|spell>",
  "trigger_event": "<one of: on_enter_battle_zone|on_attack|on_break_shield|on_destroy|on_leave_battle_zone|start_of_turn|end_of_turn|on_summon|on_battle|on_block|on_draw|on_mana_charge|on_shield_trigger|on_dragsolve|on_awaken|none>",
  "trigger_condition": "<JSON object as a string, or null>",
  "effect_action": "<one of: draw|destroy|return_to_hand|search_deck|put_to_mana|summon_free|put_to_battle_zone|put_to_shield|add_to_hand|discard|tap|untap|power_modify|cannot_attack|cannot_be_blocked|cannot_be_destroyed|win_battle|break_shield|look_at_top|shuffle|cost_reduce|cost_increase|give_keyword|banish_to_abyss|move_zone|reveal|GR_summon|copy_effect|awaken|awaken_link|dragsolve|link_release|dragon_soul_evasion|psychic_release|dragon_evasion|combine|extra_ex_life|none>",
  "effect_target": "<JSON object as a string, or null>",
  "effect_value": "<JSON value as a string, or null>",
  "is_optional": <boolean>,
  "is_replacement": <boolean>,
  "active_in_phase": <array of strings, or ["any"]>,
  "active_in_zone": <array of strings, or ["battle_zone"]>,
  "parse_confidence": <float 0.0-1.0>
}

Psychic / Dragheart flip mechanics (use these effect_action values):
- "awaken":            Psychic Creature flips to awakened (higher-cost) face (rule 805.1a).
                       Usually triggered at start_of_turn when a condition is met.
- "awaken_link":       Multiple Psychic Creatures simultaneously flip and link into a
                       Psychic Super Creature (rule 805.1c). Use effect_type "triggered".
- "dragsolve":         Dragheart Weapon/Fortress flips to Creature face (rule 807.1a).
                       Usually triggered at end_of_turn. Use trigger_event "end_of_turn".
- "link_release":      Psychic Super Creature separates; one cell returns to Hyperspatial,
                       others flip to lower face (rule 806.1b). Use effect_type "triggered".
- "dragon_soul_evasion": Replacement effect for Dragheart Super Creature leaving the BZ;
                       player chooses one Cell to return to Hyperspatial (rule 808.1b).
                       Set is_replacement=true.
- "psychic_release":   Replacement effect: Psychic Creature would leave BZ → flips to
                       lower-cost face instead (rule 805.1b). Set is_replacement=true.
- "dragon_evasion":    Replacement effect: Dragheart Creature would leave BZ → flips to
                       Weapon/Fortress face instead (rule 807.1b). Set is_replacement=true.

Trigger event guidelines for Psychic/Dragheart:
- Awaken condition checks → trigger_event: "start_of_turn"
- Dragsolution condition checks → trigger_event: "end_of_turn"
- On-enter abilities ("When you put this dragheart…") → trigger_event: "on_enter_battle_zone"
- Replacement effects (Release / Evasion) → trigger_event: "none" (they apply passively)

King Cell combine (rule 814):
- "combine":           King Cells in hand/mana combine into a King Creature. Use effect_type
                       "static" or "cost_mod" when the ability describes combine conditions.
                       Set effect_target to JSON listing required cell names/slugs when known.
- "extra_ex_life":     When a King Creature combines, it may shield a card as EX Life
                       (e.g. Volzeos Balamord). Use trigger_event "on_enter_battle_zone".

For unknown or complex effects use "none" for effect_action and lower confidence.
For trigger_condition, effect_target, and effect_value, return a valid JSON string
like "{\"amount\": 2}" or null. Do not return raw objects in those fields.

Targeting rules are important:
- If text says "a creature" or similar unrestricted wording, include legal own
  and opponent targets in effect_target scope rather than assuming opponent only.
- If a card moves from the Battle Zone to hand, use owner semantics when card text
  says "owner's hand".
- If the provided rules context conflicts with a shortcut or assumption, follow
  the rules context.
"""

class ParsedEffect(BaseModel):
    ability_index: int
    raw_text: str
    effect_type: str
    trigger_event: str
    trigger_condition: str | None
    effect_action: str
    effect_target: str | None
    effect_value: str | None
    is_optional: bool
    is_replacement: bool
    active_in_phase: list[str]
    active_in_zone: list[str]
    parse_confidence: float


class EffectsResponse(BaseModel):
    effects: list[ParsedEffect]


class ParsedCardEffects(BaseModel):
    card_index: int
    effects: list[ParsedEffect]


class BatchEffectsResponse(BaseModel):
    cards: list[ParsedCardEffects]


def _build_user_prompt(
    card_name: str,
    card_type: str,
    abilities: list[dict],
    rules_context: str = "",
) -> str:
    """Build the user message sent to the model for one card."""
    numbered_lines = []
    for index, ability in enumerate(abilities):
        face_name = ability.get("face_name") or card_name
        face_type = ability.get("face_card_type") or card_type
        raw_text = ability.get("raw_text", "")
        numbered_lines.append(
            f"{index}. Face: {face_name} ({face_type})\n"
            f"   Ability: {raw_text}"
        )

    rules_block = ""
    if rules_context:
        rules_block = f"\n\nRules context:\n{rules_context}\n"

    ability_block = "\n".join(numbered_lines)
    return (
        f"Card: {card_name}\n"
        f"Type: {card_type}\n\n"
        f"Abilities:\n{ability_block}\n"
        f"{rules_block}\n"
        f"Parse each ability into the JSON object format: {{\"effects\": [...]}}. "
        f"Use the numbered index as ability_index and use only the Ability text as raw_text."
    )


def _build_batch_user_prompt(cards: list[dict]) -> str:
    """Build one prompt containing multiple cards."""
    card_blocks = []
    for card_index, card in enumerate(cards):
        abilities = card["abilities"]
        ability_lines = []
        for ability_index, ability in enumerate(abilities):
            face_name = ability.get("face_name") or card["card_name"]
            face_type = ability.get("face_card_type") or card["card_type"]
            ability_lines.append(
                f"{ability_index}. Face: {face_name} ({face_type})\n"
                f"   Ability: {ability.get('raw_text', '')}"
            )

        rules_block = ""
        if card.get("rules_context"):
            rules_block = f"\nRules context:\n{card['rules_context']}"

        card_blocks.append(
            f"Card index: {card_index}\n"
            f"Card: {card['card_name']}\n"
            f"Type: {card['card_type']}\n"
            f"Abilities:\n{chr(10).join(ability_lines)}"
            f"{rules_block}"
        )

    return (
        "Parse each card independently.\n\n"
        + "\n\n---\n\n".join(card_blocks)
        + "\n\nReturn exactly this JSON shape:\n"
        '{"cards":[{"card_index":0,"effects":[...]},{"card_index":1,"effects":[...]}]}\n'
        "Use ability_index as the 0-based index within that card, not across all cards."
    )


def _model_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _jsonb_param(value):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value))
        except json.JSONDecodeError:
            return json.dumps({"text": value})
    return json.dumps(value)


# ── LLM call ──────────────────────────────────────────────────────────────────

def _usage_to_dict(usage) -> dict[str, int]:
    if usage is None:
        return dict(EMPTY_USAGE)
    prompt_tokens = (
        getattr(usage, "prompt_tokens", None)
        or getattr(usage, "input_tokens", None)
        or 0
    )
    completion_tokens = (
        getattr(usage, "completion_tokens", None)
        or getattr(usage, "output_tokens", None)
        or 0
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
    }


def _response_text(response) -> str:
    """Return the model response content as a JSON string."""
    choice = response.choices[0]
    raw_content = choice.message.content
    if isinstance(raw_content, str):
        cleaned = _strip_markdown_json(raw_content)
        if cleaned:
            return cleaned
        finish_reason = getattr(choice, "finish_reason", None)
        usage = _usage_to_dict(getattr(response, "usage", None))
        raise ValueError(
            "LLM returned empty content "
            f"(finish_reason={finish_reason}, total_tokens={usage['total_tokens']}). "
            "For OpenAI GPT-5 models, increase --max-tokens because reasoning tokens "
            "count against max_completion_tokens."
        )
    if isinstance(raw_content, list):
        parts = []
        for part in raw_content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(getattr(part, "text", "") or getattr(part, "content", "")))
        cleaned = _strip_markdown_json("".join(parts))
        if cleaned:
            return cleaned
    return json.dumps(raw_content or {})


def _strip_markdown_json(text: str) -> str:
    """OpenRouter models often return JSON inside ```json fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _parse_effect_response(response) -> tuple[list[dict], dict[str, int]]:
    """Validate OpenRouter response JSON and return effects plus token usage."""
    data = json.loads(_response_text(response))
    parsed = EffectsResponse(**data)
    effects = [_model_to_dict(effect) for effect in parsed.effects]
    usage = _usage_to_dict(getattr(response, "usage", None))
    return effects, usage


def _parse_effect_text(text: str, usage: dict[str, int]) -> tuple[list[dict], dict[str, int]]:
    """Validate raw JSON text and return effects plus usage."""
    data = json.loads(_strip_markdown_json(text))
    parsed = EffectsResponse(**data)
    effects = [_model_to_dict(effect) for effect in parsed.effects]
    return effects, usage


def _parse_batch_effect_response(response) -> tuple[dict[int, list[dict]], dict[str, int]]:
    """Validate a multi-card response and return {card_index: effects}."""
    data = json.loads(_response_text(response))
    usage = _usage_to_dict(getattr(response, "usage", None))

    if "cards" in data:
        parsed = BatchEffectsResponse(**data)
        return {
            card.card_index: [_model_to_dict(effect) for effect in card.effects]
            for card in parsed.cards
        }, usage

    # Helpful fallback when cards_per_call=1 and the model returns the old shape.
    parsed = EffectsResponse(**data)
    return {0: [_model_to_dict(effect) for effect in parsed.effects]}, usage


def _parse_batch_effect_text(text: str, usage: dict[str, int]) -> tuple[dict[int, list[dict]], dict[str, int]]:
    """Validate raw multi-card JSON text and return {card_index: effects}."""
    data = json.loads(_strip_markdown_json(text))
    if "cards" in data:
        parsed = BatchEffectsResponse(**data)
        return {
            card.card_index: [_model_to_dict(effect) for effect in card.effects]
            for card in parsed.cards
        }, usage

    parsed = EffectsResponse(**data)
    return {0: [_model_to_dict(effect) for effect in parsed.effects]}, usage


def _ollama_chat(
    ollama_host: str,
    model: str,
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, int]]:
    """Send one chat request to a local/cloud Ollama model."""
    url = ollama_host.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=100000) as response:
        data = json.loads(response.read().decode("utf-8"))

    text = data.get("message", {}).get("content", "{}")
    usage = {
        "prompt_tokens": int(data.get("prompt_eval_count") or 0),
        "completion_tokens": int(data.get("eval_count") or 0),
        "total_tokens": int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0),
    }
    return text, usage


def _is_openai_reasoning_model(model: str) -> bool:
    model_name = model.lower()
    return model_name.startswith(("gpt-5", "o1", "o3", "o4"))


def _openai_chat_completion(
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
):
    """Send an OpenAI chat request with settings that favor concise JSON output."""
    kwargs = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": max_tokens,
    }
    if _is_openai_reasoning_model(model):
        kwargs["reasoning_effort"] = "minimal"
    return client.chat.completions.create(**kwargs)


def _nvidia_chat_completion(
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
):
    """Send one direct NVIDIA API request through its OpenAI-compatible endpoint."""
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        top_p=0.95,
        max_tokens=max_tokens,
        extra_body={
            "chat_template_kwargs": {"enable_thinking": True},
            "reasoning_budget": max_tokens,
        },
        stream=False,
    )


def _parse_with_llm(
    card_name: str,
    card_type: str,
    abilities: list[dict],
    client: Any,
    model: str = "nvidia/nemotron-3-super-120b-a12b:free",
    provider: str = "openrouter",
    ollama_host: str = "http://localhost:11434",
    rules_context: str = "",
    retries: int = 3,
    max_tokens: int = 2048,
) -> Optional[tuple[list[dict], dict[str, int]]]:
    if not abilities:
        return [], dict(EMPTY_USAGE)

    prompt = _build_user_prompt(card_name, card_type, abilities, rules_context)
    logger.info("  Model: %s (%s)", model, provider)

    for attempt in range(retries):
        attempt_number = attempt + 1
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            if provider == "ollama":
                text, usage = _ollama_chat(ollama_host, model, messages)
                effects, usage = _parse_effect_text(text, usage)
            elif provider == "openai":
                response = _openai_chat_completion(client, model, messages, max_tokens)
                effects, usage = _parse_effect_response(response)
            elif provider == "nvidia":
                response = _nvidia_chat_completion(client, model, messages, max_tokens)
                effects, usage = _parse_effect_response(response)
            elif provider == "local-hf":
                text, usage = client.chat(messages, max_tokens)
                logger.info("  Text: %s", text)
                effects, usage = _parse_effect_text(text, usage)
            else:
                response = client.chat.send(model=model, messages=messages)
                logger.info("  Response: %s", json.dumps(response, indent=2))
                effects, usage = _parse_effect_response(response)
            logger.info(
                "  Token usage for %s: prompt=%s completion=%s total=%s",
                card_name,
                usage["prompt_tokens"],
                usage["completion_tokens"],
                usage["total_tokens"],
            )
            return effects, usage

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(
                "Structured parse error for %s (attempt %s/%s): %s",
                card_name,
                attempt_number,
                retries,
                e,
            )
            time.sleep(2 ** attempt)
        except openrouter_errors.TooManyRequestsResponseError as e:
            wait = 60 * attempt_number
            logger.warning(
                "Rate limited by OpenRouter for %s; waiting %ss before retry: %s",
                card_name,
                wait,
                e,
            )
            time.sleep(wait)
        except (
            openrouter_errors.ProviderOverloadedResponseError,
            openrouter_errors.ServiceUnavailableResponseError,
            openrouter_errors.BadGatewayResponseError,
            openrouter_errors.EdgeNetworkTimeoutResponseError,
            openrouter_errors.RequestTimeoutResponseError,
            openrouter_errors.ResponseValidationError,
        ) as e:
            wait = 15 * attempt_number
            logger.warning(
                "Temporary OpenRouter/provider or response-validation error for %s; "
                "waiting %ss before retry: %s",
                card_name,
                wait,
                e,
            )
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as e:
            wait = 15 * attempt_number
            logger.warning(
                "Temporary Ollama/provider error for %s; waiting %ss before retry: %s",
                card_name,
                wait,
                e,
            )
            time.sleep(wait)
        except openai.RateLimitError as e:
            wait = 60 * attempt_number
            logger.warning(
                "Rate limited by OpenAI for %s; waiting %ss before retry: %s",
                card_name,
                wait,
                e,
            )
            time.sleep(wait)
        except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as e:
            wait = 15 * attempt_number
            logger.warning(
                "Temporary OpenAI error for %s; waiting %ss before retry: %s",
                card_name,
                wait,
                e,
            )
            time.sleep(wait)
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.BadRequestError) as e:
            logger.error("Non-retryable OpenAI error for %s: %s", card_name, e)
            return None
        except (
            openrouter_errors.UnauthorizedResponseError,
            openrouter_errors.ForbiddenResponseError,
            openrouter_errors.PaymentRequiredResponseError,
        ) as e:
            logger.error("Non-retryable OpenRouter auth/billing error for %s: %s", card_name, e)
            return None
        except (RuntimeError, ConnectionError) as e:
            logger.error("API error for %s: %s", card_name, e)
            time.sleep(5 * (attempt + 1))

    return None


def _parse_batch_with_llm(
    cards: list[dict],
    client: Any,
    model: str,
    provider: str,
    ollama_host: str,
    retries: int,
    max_tokens: int,
) -> Optional[tuple[dict[int, list[dict]], dict[str, int]]]:
    """Ask the selected LLM provider to parse multiple cards in one request."""
    if not cards:
        return {}, dict(EMPTY_USAGE)

    prompt = _build_batch_user_prompt(cards)
    logger.info("  Model: %s (%s)", model, provider)
    logger.info("  Cards in this LLM call: %s", len(cards))

    for attempt in range(retries):
        attempt_number = attempt + 1
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            if provider == "ollama":
                text, usage = _ollama_chat(ollama_host, model, messages)
                effects_by_card, usage = _parse_batch_effect_text(text, usage)
            elif provider == "openai":
                response = _openai_chat_completion(client, model, messages, max_tokens)
                effects_by_card, usage = _parse_batch_effect_response(response)
            elif provider == "nvidia":
                response = _nvidia_chat_completion(client, model, messages, max_tokens)
                effects_by_card, usage = _parse_batch_effect_response(response)
            elif provider == "local-hf":
                text, usage = client.chat(messages, max_tokens)
                effects_by_card, usage = _parse_batch_effect_text(text, usage)
            else:
                response = client.chat.send(model=model, messages=messages)
                effects_by_card, usage = _parse_batch_effect_response(response)
            logger.info(
                "  Token usage: prompt=%s completion=%s total=%s",
                usage["prompt_tokens"],
                usage["completion_tokens"],
                usage["total_tokens"],
            )
            return effects_by_card, usage

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(
                "Structured batch parse error (attempt %s/%s): %s",
                attempt_number,
                retries,
                e,
            )
            time.sleep(2 ** attempt)
        except openrouter_errors.TooManyRequestsResponseError as e:
            wait = 60 * attempt_number
            logger.warning("Rate limited by OpenRouter; waiting %ss before retry: %s", wait, e)
            time.sleep(wait)
        except (
            openrouter_errors.ProviderOverloadedResponseError,
            openrouter_errors.ServiceUnavailableResponseError,
            openrouter_errors.BadGatewayResponseError,
            openrouter_errors.EdgeNetworkTimeoutResponseError,
            openrouter_errors.RequestTimeoutResponseError,
            openrouter_errors.ResponseValidationError,
        ) as e:
            wait = 15 * attempt_number
            logger.warning(
                "Temporary OpenRouter/provider error; waiting %ss before retry: %s",
                wait,
                e,
            )
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as e:
            wait = 15 * attempt_number
            logger.warning("Temporary Ollama/provider error; waiting %ss before retry: %s", wait, e)
            time.sleep(wait)
        except openai.RateLimitError as e:
            wait = 60 * attempt_number
            logger.warning("Rate limited by OpenAI; waiting %ss before retry: %s", wait, e)
            time.sleep(wait)
        except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as e:
            wait = 15 * attempt_number
            logger.warning("Temporary OpenAI error; waiting %ss before retry: %s", wait, e)
            time.sleep(wait)
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.BadRequestError) as e:
            logger.error("Non-retryable OpenAI error: %s", e)
            return None
        except (
            openrouter_errors.UnauthorizedResponseError,
            openrouter_errors.ForbiddenResponseError,
            openrouter_errors.PaymentRequiredResponseError,
        ) as e:
            logger.error("Non-retryable OpenRouter auth/billing error: %s", e)
            return None
        except (RuntimeError, ConnectionError) as e:
            logger.error("API error: %s", e)
            time.sleep(5 * attempt_number)

    return None


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _ability_record(
    raw_text: str,
    face_index: Optional[int] = None,
    face_name: Optional[str] = None,
    face_card_type: Optional[str] = None,
) -> dict:
    return {
        "raw_text": raw_text.strip(),
        "face_index": face_index,
        "face_name": face_name,
        "face_card_type": face_card_type,
    }


def _split_ability_text(text: str) -> list[str]:
    return [
        "■ " + line.strip()
        for line in str(text).split("■")
        if len(line.strip()) > 5
    ]


def _extract_ability_records_from_raw_text(raw_text: str) -> list[dict]:
    """Extract face-aware ability records from JSON raw_text or legacy text."""
    if not raw_text:
        return []

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        faces = parsed.get("faces")
        if isinstance(faces, list) and faces:
            records = []
            for face_index, face in enumerate(faces):
                if not isinstance(face, dict):
                    continue
                face_name = face.get("name") or None
                face_card_type = face.get("card_type") or None
                abilities = face.get("abilities")
                if not isinstance(abilities, list):
                    raw_fields = face.get("fields")
                    fields = raw_fields if isinstance(raw_fields, dict) else {}
                    abilities = _split_ability_text(fields.get("english text", ""))
                for ability in abilities:
                    ability_text = str(ability).strip()
                    if "■" in ability_text and len(ability_text) > 5:
                        records.append(
                            _ability_record(
                                raw_text=ability_text,
                                face_index=face_index,
                                face_name=face_name,
                                face_card_type=face_card_type,
                            )
                        )
            if records:
                return records

        abilities = parsed.get("abilities")
        if isinstance(abilities, list):
            return [
                _ability_record(str(ability).strip())
                for ability in abilities
                if "■" in str(ability) and len(str(ability).strip()) > 5
            ]

        fields = parsed.get("fields")
        if isinstance(fields, dict):
            text = fields.get("english text") or fields.get("english_text") or ""
            return [
                _ability_record(line)
                for line in _split_ability_text(text)
            ]

    return [
        _ability_record(line.strip())
        for line in raw_text.split("\n")
        if "■" in line and len(line.strip()) > 5
    ]

def _fetch_pending_cards(conn, limit: int) -> list[dict]:
    """Get cards that have been scraped but not yet parsed."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT c.id, c.name, c.card_type, c.slug, c.raw_text,
                   cu.url
            FROM cards c
            JOIN card_urls cu ON cu.url = c.source_url
            WHERE cu.status = 'scraped'
            order by c.id desc
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def _fetch_unparsed_raw_text_cards_by_slugs(conn, slugs: list[str], limit: int | None = None) -> list[dict]:
    """Get requested cards with raw text whose URL has not been marked parsed."""
    if not slugs:
        return []

    unique_slugs = list(dict.fromkeys(slugs))
    sql = """
        SELECT c.id, c.name, c.card_type, c.slug, c.raw_text,
               cu.url
        FROM cards c
        JOIN card_urls cu ON cu.url = c.source_url
        WHERE c.slug = ANY(%s)
          AND cu.status <> 'parsed'
          AND c.raw_text IS NOT NULL
          AND btrim(c.raw_text) <> ''
        ORDER BY array_position(%s::text[], c.slug)
    """
    params: list[Any] = [unique_slugs, unique_slugs]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def reset_psychic_dragheart_for_reparse(dsn: str, dry_run: bool = False) -> int:
    """
    Reset all Psychic and Dragheart card URLs from 'parsed' → 'scraped' so that
    parse_pending_cards() will re-parse them with the updated SYSTEM_PROMPT that
    includes the new awaken/dragsolve/link_release/psychic_release/dragon_evasion
    effect_action values.

    Returns the number of rows reset.
    """
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE card_urls cu
                SET    status = 'scraped', parsed_at = NULL
                FROM   cards c
                WHERE  cu.url = c.source_url
                  AND  cu.status = 'parsed'
                  AND  (
                         c.card_subtype = 'Psychic'
                      OR c.card_type    ILIKE 'Dragheart%%'
                      OR c.card_type    = 'Super Creature'
                  )
                """,
            )
            count = cur.rowcount
            if dry_run:
                conn.rollback()
                logger.info("[dry-run] Would reset %s Psychic/Dragheart card URLs for re-parse", count)
            else:
                conn.commit()
                logger.info("Reset %s Psychic/Dragheart card URLs to 'scraped' for re-parse", count)
        return count
    finally:
        conn.close()


def find_missing_card_slugs(dsn: str, slugs: list[str]) -> list[str]:
    """Return requested slugs that do not exist in the cards table."""
    unique_slugs = list(dict.fromkeys(slugs))
    if not unique_slugs:
        return []

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT slug FROM cards WHERE slug = ANY(%s)", (unique_slugs,))
            found = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    return [slug for slug in unique_slugs if slug not in found]


def _save_effects(conn, card_id: int, effects: list[dict]):
    """Delete old effects for card and insert fresh parsed ones."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM card_effects WHERE card_id = %s", (card_id,))

        for eff in effects:
            cur.execute(
                """
                INSERT INTO card_effects (
                    card_id, face_index, face_name, ability_index, raw_text, effect_type, trigger_event,
                    trigger_condition, effect_action, effect_target, effect_value,
                    is_optional, is_replacement, active_in_phase, active_in_zone,
                    parse_confidence
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    card_id,
                    eff.get("face_index"),
                    eff.get("face_name"),
                    eff.get("ability_index", 0),
                    eff.get("raw_text", ""),
                    eff.get("effect_type"),
                    eff.get("trigger_event"),
                    _jsonb_param(eff.get("trigger_condition")),
                    eff.get("effect_action"),
                    _jsonb_param(eff.get("effect_target")),
                    _jsonb_param(eff.get("effect_value")),
                    eff.get("is_optional", False),
                    eff.get("is_replacement", False),
                    eff.get("active_in_phase", ["any"]),
                    eff.get("active_in_zone", ["battle_zone"]),
                    eff.get("parse_confidence", 0.5),
                ),
            )
    conn.commit()


def _mark_parsed(conn, card_url: str):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE card_urls SET status='parsed', parsed_at=NOW() WHERE url=%s",
            (card_url,),
        )
    conn.commit()


def _mark_parse_error(conn, card_url: str):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE card_urls SET status='error' WHERE url=%s",
            (card_url,),
        )
    conn.commit()


def _copy_ability_metadata(effects: list[dict], ability_records: list[dict]) -> None:
    """Copy face/raw-text metadata from scraped abilities onto parsed effects."""
    for effect in effects:
        index = effect.get("ability_index", 0)
        if not isinstance(index, int) or not 0 <= index < len(ability_records):
            continue

        source = ability_records[index]
        effect["raw_text"] = source["raw_text"]
        effect["face_index"] = source["face_index"]
        effect["face_name"] = source["face_name"]


def _wait_between_cards(delay_between: float, should_stop: Optional[Callable[[], bool]]) -> bool:
    """
    Sleep between API calls.

    Returns True if parsing should stop while waiting.
    """
    sleep_until = time.monotonic() + delay_between + random.uniform(0, 0.3)
    while time.monotonic() < sleep_until:
        if should_stop and should_stop():
            return True
        time.sleep(min(0.1, sleep_until - time.monotonic()))
    return False


def _process_one_card(
    conn,
    client: Any,
    card_row: dict,
    *,
    model: str,
    provider: str,
    ollama_host: str,
    rules_context_config: RulesContextConfig | None,
    retries: int,
    max_tokens: int,
) -> tuple[str, dict[str, int]]:
    """
    Parse one scraped card.

    Returns:
        (status, usage)
        status is one of: parsed, skipped, error
    """
    card_id = card_row["id"]
    card_name = card_row["name"]
    card_type = card_row["card_type"] or "Unknown"
    card_url = card_row["url"]

    ability_records = _extract_ability_records_from_raw_text(card_row["raw_text"] or "")
    if not ability_records:
        logger.info("  Skipping %s (no abilities)", card_name)
        _mark_parsed(conn, card_url)
        return "skipped", dict(EMPTY_USAGE)

    logger.info("  Parsing %s (%s abilities)", card_name, len(ability_records))
    rules_context = build_rules_context(
        card_name=card_name,
        card_type=card_type,
        abilities=ability_records,
        config=rules_context_config,
    )
    if rules_context:
        logger.info("  Added rules context for %s", card_name)

    parsed_result = _parse_with_llm(
        card_name=card_name,
        card_type=card_type,
        abilities=ability_records,
        client=client,
        model=model,
        provider=provider,
        ollama_host=ollama_host,
        rules_context=rules_context,
        retries=retries,
        max_tokens=max_tokens,
    )

    if parsed_result is None:
        logger.error("  LLM parse failed for %s", card_name)
        _mark_parse_error(conn, card_url)
        return "error", dict(EMPTY_USAGE)

    effects, usage = parsed_result
    _copy_ability_metadata(effects, ability_records)
    _save_effects(conn, card_id, effects)
    _mark_parsed(conn, card_url)
    logger.info("  ✓ %s: %s effects stored", card_name, len(effects))
    return "parsed", usage


def _chunks(items: list[dict], size: int):
    """Yield small groups from a list."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _prepare_card_for_batch(conn, card_row: dict, rules_context_config: RulesContextConfig | None) -> tuple[str, dict | None]:
    """Prepare one card for a multi-card LLM request."""
    card_name = card_row["name"]
    card_type = card_row["card_type"] or "Unknown"
    card_url = card_row["url"]
    ability_records = _extract_ability_records_from_raw_text(card_row["raw_text"] or "")

    if not ability_records:
        logger.info("  Skipping %s (no abilities)", card_name)
        _mark_parsed(conn, card_url)
        return "skipped", None

    logger.info("  Queued %s (%s abilities)", card_name, len(ability_records))
    rules_context = build_rules_context(
        card_name=card_name,
        card_type=card_type,
        abilities=ability_records,
        config=rules_context_config,
    )
    if rules_context:
        logger.info("  Added rules context for %s", card_name)

    return "queued", {
        "card_id": card_row["id"],
        "card_name": card_name,
        "card_type": card_type,
        "card_url": card_url,
        "abilities": ability_records,
        "rules_context": rules_context,
    }


def _process_card_batch(
    conn,
    client: Any,
    card_rows: list[dict],
    *,
    model: str,
    provider: str,
    ollama_host: str,
    rules_context_config: RulesContextConfig | None,
    retries: int,
    max_tokens: int,
) -> dict[str, int]:
    """Parse and save a group of cards using one LLM request."""
    counts = {"parsed": 0, "errors": 0, "skipped": 0, **dict(EMPTY_USAGE)}
    cards_for_llm: list[dict] = []

    for card_row in card_rows:
        status, prepared = _prepare_card_for_batch(conn, card_row, rules_context_config)
        if status == "skipped":
            counts["skipped"] += 1
        elif prepared is not None:
            cards_for_llm.append(prepared)

    if not cards_for_llm:
        return counts

    if provider == "local-hf":
        for card in cards_for_llm:
            parsed_result = _parse_with_llm(
                card_name=card["card_name"],
                card_type=card["card_type"],
                abilities=card["abilities"],
                client=client,
                model=model,
                provider=provider,
                ollama_host=ollama_host,
                rules_context=card.get("rules_context", ""),
                retries=retries,
                max_tokens=max_tokens,
            )
            if parsed_result is None:
                _mark_parse_error(conn, card["card_url"])
                counts["errors"] += 1
                continue

            effects, usage = parsed_result
            counts["prompt_tokens"] += usage["prompt_tokens"]
            counts["completion_tokens"] += usage["completion_tokens"]
            counts["total_tokens"] += usage["total_tokens"]
            _copy_ability_metadata(effects, card["abilities"])
            _save_effects(conn, card["card_id"], effects)
            _mark_parsed(conn, card["card_url"])
            counts["parsed"] += 1
            logger.info("  ✓ %s: %s effects stored", card["card_name"], len(effects))
        return counts

    parsed_result = _parse_batch_with_llm(
        cards=cards_for_llm,
        client=client,
        model=model,
        provider=provider,
        ollama_host=ollama_host,
        retries=retries,
        max_tokens=max_tokens,
    )

    if parsed_result is None:
        for card in cards_for_llm:
            _mark_parse_error(conn, card["card_url"])
        counts["errors"] += len(cards_for_llm)
        return counts

    effects_by_card, usage = parsed_result
    counts["prompt_tokens"] += usage["prompt_tokens"]
    counts["completion_tokens"] += usage["completion_tokens"]
    counts["total_tokens"] += usage["total_tokens"]

    for card_index, card in enumerate(cards_for_llm):
        effects = effects_by_card.get(card_index)
        if effects is None:
            logger.error("  LLM response missing effects for %s", card["card_name"])
            _mark_parse_error(conn, card["card_url"])
            counts["errors"] += 1
            continue

        _copy_ability_metadata(effects, card["abilities"])
        _save_effects(conn, card["card_id"], effects)
        _mark_parsed(conn, card["card_url"])
        counts["parsed"] += 1
        logger.info("  ✓ %s: %s effects stored", card["card_name"], len(effects))

    return counts


# ── Main entry point ───────────────────────────────────────────────────────────

def _process_parallel_batches(
    dsn: str,
    client: Any,
    card_groups: list[list[dict]],
    *,
    max_workers: int,
    model: str,
    provider: str,
    ollama_host: str,
    rules_context_config: "RulesContextConfig | None",
    retries: int,
    max_tokens: int,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict[str, int]:
    """
    Run card groups through the LLM with a rolling worker pool.

    Each group becomes one LLM request. When one request finishes, the next
    group is submitted immediately, keeping up to max_workers requests in
    flight until the queue is exhausted. DB writes are done inside each worker
    using a per-thread connection so psycopg2 is never shared across threads.
    The OpenAI/NVIDIA client is shared because it is thread-safe.

    Args:
        card_groups: list of card-row groups; each group becomes one LLM call.
        max_workers: maximum LLM calls in flight at a time.
        max_tokens: full output-token budget for each LLM request, not shared
            across parallel calls.
    Returns:
        Merged counts dict {"parsed", "errors", "skipped", token counts}.
    """
    merged: dict[str, int] = {"parsed": 0, "errors": 0, "skipped": 0, **dict(EMPTY_USAGE)}
    if not card_groups:
        return merged

    def _worker(card_rows: list[dict]) -> dict[str, int]:
        conn = psycopg2.connect(dsn)
        try:
            return _process_card_batch(
                conn=conn,
                client=client,
                card_rows=card_rows,
                model=model,
                provider=provider,
                ollama_host=ollama_host,
                rules_context_config=rules_context_config,
                retries=retries,
                max_tokens=max_tokens,
            )
        finally:
            conn.close()

    max_workers = max(1, min(max_workers, len(card_groups)))
    group_iter = iter(enumerate(card_groups))

    def _submit_next(executor: ThreadPoolExecutor, futures: dict) -> bool:
        if should_stop and should_stop():
            return False
        try:
            group_index, group = next(group_iter)
        except StopIteration:
            return False
        futures[executor.submit(_worker, group)] = group_index
        logger.info(
            "  Started LLM request batch %s (%s cards, %s/%s in flight, max_tokens=%s)",
            group_index + 1,
            len(group),
            len(futures),
            max_workers,
            max_tokens,
        )
        return True

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict = {}
        for _ in range(max_workers):
            if not _submit_next(executor, futures):
                break

        while futures:
            done, _ = futures_wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future, None)
                try:
                    result = future.result()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.error("Parallel batch worker raised an exception: %s", exc)
                    result = {"parsed": 0, "errors": 1, "skipped": 0, **dict(EMPTY_USAGE)}
                for key, value in result.items():
                    merged[key] = merged.get(key, 0) + value

                if not _submit_next(executor, futures) and should_stop and should_stop():
                    logger.info("Stop requested; no new parallel LLM batches will be started")

    return merged


def parse_pending_cards(
    dsn: str,
    api_key: str | None,
    batch_size: int = 50,
    cards_per_call: int = 3,
    delay_between: float = 0.5,
    model: str = "nvidia/nemotron-3-super-120b-a12b:free",
    base_url: str | None = None,
    provider: str = "openrouter",
    ollama_host: str = "http://localhost:11434",
    rules_context_config: RulesContextConfig | None = None,
    retries: int = 5,
    max_tokens: int = 2048,
    local_hf_timeout: float = DEFAULT_LOCAL_HF_TIMEOUT,
    parallel_calls: int | None = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict:
    """
    Parse all cards with status='scraped' using OpenAI.

    Args:
        dsn:             PostgreSQL DSN
        api_key:         API key, needed for OpenRouter/OpenAI/NVIDIA
        batch_size:      How many cards to process per run
        cards_per_call:  How many cards to parse in one LLM request (default 3)
        delay_between:   Seconds to sleep between API call rounds
        model:           Provider model name, or local LoRA adapter folder for local-hf
        base_url:        NVIDIA base URL when provider is "nvidia"
        provider:        "openrouter", "openai", "ollama", "local-hf", or "nvidia"
        ollama_host:     Ollama server URL when provider is "ollama"
        retries:         LLM retries per card for retryable provider errors
        max_tokens:      Maximum output tokens requested from the provider
        local_hf_timeout: Maximum local generation seconds when provider is "local-hf"
        parallel_calls:  How many LLM requests to keep in flight.
                         Defaults to 2 for the nvidia provider, 1 for all others.
                         Each parallel call processes cards_per_call cards, so
                         nvidia default concurrency is 3 cards × 2 calls.

    Returns:
        {"parsed": N, "errors": M, "skipped": K}
    """
    if provider not in LLM_PROVIDERS:
        raise ValueError(f"provider must be one of: {', '.join(LLM_PROVIDERS)}")
    if provider in ("openrouter", "openai", "nvidia") and (not api_key or not api_key.strip()):
        raise ValueError(
            "Missing LLM API key. Set OPENROUTER_API_KEY/OPENAI_API_KEY/NVIDIA_API_KEY or pass --api-key."
        )
    if cards_per_call < 1:
        raise ValueError("cards_per_call must be at least 1")

    effective_parallel = parallel_calls if parallel_calls is not None else (2 if provider == "nvidia" else 1)
    effective_parallel = max(1, effective_parallel)

    conn = psycopg2.connect(dsn)

    counts = {
        "parsed": 0,
        "errors": 0,
        "skipped": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    try:
        if provider == "openrouter":
            client_context = OpenRouter(api_key=api_key)
        elif provider == "openai":
            client_context = _ClientContext(OpenAI(api_key=api_key))
        elif provider == "nvidia":
            client_context = _ClientContext(
                OpenAI(api_key=api_key, base_url=base_url or DEFAULT_NVIDIA_BASE_URL)
            )
        elif provider == "local-hf":
            client_context = _LocalHfClient(model, generation_timeout=local_hf_timeout)
        else:
            client_context = _NullLlmClient()
        with client_context as client:
            cards = _fetch_pending_cards(conn, batch_size)
            logger.info(
                "Found %s scraped cards pending LLM parse (cards_per_call=%s, parallel_calls=%s)",
                len(cards), cards_per_call, effective_parallel,
            )

            if effective_parallel > 1:
                batch_counts = _process_parallel_batches(
                    dsn=dsn,
                    client=client,
                    card_groups=list(_chunks(cards, cards_per_call)),
                    max_workers=effective_parallel,
                    model=model,
                    provider=provider,
                    ollama_host=ollama_host,
                    rules_context_config=rules_context_config,
                    retries=retries,
                    max_tokens=max_tokens,
                    should_stop=should_stop,
                )
                for key, value in batch_counts.items():
                    counts[key] = counts.get(key, 0) + value
            else:
                for card_group in _chunks(cards, cards_per_call):
                    if should_stop and should_stop():
                        logger.info("Stop requested; ending effect parsing after current checkpoint")
                        break

                    batch_counts = _process_card_batch(
                        conn=conn,
                        client=client,
                        card_rows=card_group,
                        model=model,
                        provider=provider,
                        ollama_host=ollama_host,
                        rules_context_config=rules_context_config,
                        retries=retries,
                        max_tokens=max_tokens,
                    )

                    for key, value in batch_counts.items():
                        counts[key] = counts.get(key, 0) + value

                    if _wait_between_cards(delay_between, should_stop):
                        logger.info("Stop requested; ending effect parsing before next card")
                        return counts

    finally:
        conn.close()

    return counts


def parse_cards_by_slugs(
    dsn: str,
    slugs: list[str],
    api_key: str | None,
    cards_per_call: int = 3,
    delay_between: float = 0.5,
    model: str = "nvidia/nemotron-3-super-120b-a12b:free",
    provider: str = "openrouter",
    ollama_host: str = "http://localhost:11434",
    rules_context_config: RulesContextConfig | None = None,
    retries: int = 5,
    max_tokens: int = 2048,
    local_hf_timeout: float = DEFAULT_LOCAL_HF_TIMEOUT,
    parallel_calls: int | None = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict:
    """Parse requested card slugs with raw text whose card URL is not parsed.

    Args:
        parallel_calls: How many LLM requests to keep in flight.
                        Defaults to 2 for the nvidia provider, 1 for all others.
    """
    if provider not in LLM_PROVIDERS:
        raise ValueError(f"provider must be one of: {', '.join(LLM_PROVIDERS)}")
    if provider in ("openrouter", "openai", "nvidia") and (not api_key or not api_key.strip()):
        raise ValueError(
            "Missing LLM API key. Set OPENROUTER_API_KEY/OPENAI_API_KEY/NVIDIA_API_KEY or pass --api-key."
        )
    if cards_per_call < 1:
        raise ValueError("cards_per_call must be at least 1")

    effective_parallel = parallel_calls if parallel_calls is not None else (2 if provider == "nvidia" else 1)
    effective_parallel = max(1, effective_parallel)

    conn = psycopg2.connect(dsn)
    counts = {
        "parsed": 0,
        "errors": 0,
        "skipped": 0,
        "parsed_or_missing_raw_text": 0,
        "already_parsed": 0,
        "requested": len(list(dict.fromkeys(slugs))),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    try:
        if provider == "openrouter":
            client_context = OpenRouter(api_key=api_key)
        elif provider == "openai":
            client_context = _ClientContext(OpenAI(api_key=api_key))
        elif provider == "nvidia":
            client_context = _ClientContext(
                OpenAI(api_key=api_key, base_url=DEFAULT_NVIDIA_BASE_URL)
            )
        elif provider == "local-hf":
            client_context = _LocalHfClient(model, generation_timeout=local_hf_timeout)
        else:
            client_context = _NullLlmClient()

        with client_context as client:
            cards = _fetch_unparsed_raw_text_cards_by_slugs(conn, slugs)
            counts["parsed_or_missing_raw_text"] = counts["requested"] - len(cards)
            counts["already_parsed"] = counts["parsed_or_missing_raw_text"]
            logger.info(
                "Found %s requested deck cards with raw text and status!=parsed (%s parsed or missing raw text)",
                len(cards),
                counts["parsed_or_missing_raw_text"],
            )

            if effective_parallel > 1:
                batch_counts = _process_parallel_batches(
                    dsn=dsn,
                    client=client,
                    card_groups=list(_chunks(cards, cards_per_call)),
                    max_workers=effective_parallel,
                    model=model,
                    provider=provider,
                    ollama_host=ollama_host,
                    rules_context_config=rules_context_config,
                    retries=retries,
                    max_tokens=max_tokens,
                    should_stop=should_stop,
                )
                for key, value in batch_counts.items():
                    counts[key] = counts.get(key, 0) + value
            else:
                for card_group in _chunks(cards, cards_per_call):
                    if should_stop and should_stop():
                        logger.info("Stop requested; ending effect parsing after current checkpoint")
                        break

                    batch_counts = _process_card_batch(
                        conn=conn,
                        client=client,
                        card_rows=card_group,
                        model=model,
                        provider=provider,
                        ollama_host=ollama_host,
                        rules_context_config=rules_context_config,
                        retries=retries,
                        max_tokens=max_tokens,
                    )

                    for key, value in batch_counts.items():
                        counts[key] = counts.get(key, 0) + value

                    if _wait_between_cards(delay_between, should_stop):
                        logger.info("Stop requested; ending effect parsing before next card")
                        return counts

    finally:
        conn.close()

    return counts
