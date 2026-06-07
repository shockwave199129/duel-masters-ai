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

# ── Prompt ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert Duel Masters card game rules engine parser.
Given a card's raw ability text lines (each starting with ■), you output a JSON object
with an "effects" array where each element is the structured representation of one ability.

Each effect object must have these exact fields:
{
  "ability_index": <int, 0-based position in the list>,
  "raw_text": "<exact input line>",
  "effect_type": "<one of: keyword|triggered|activated|static|replacement|cost_mod|spell>",
  "trigger_event": "<one of: on_enter_battle_zone|on_attack|on_break_shield|on_destroy|on_leave_battle_zone|start_of_turn|end_of_turn|on_summon|on_battle|on_block|on_draw|on_mana_charge|on_shield_trigger|none>",
  "trigger_condition": "<JSON object as a string, or null>",
  "effect_action": "<one of: draw|destroy|return_to_hand|search_deck|put_to_mana|summon_free|put_to_battle_zone|put_to_shield|add_to_hand|discard|tap|untap|power_modify|cannot_attack|cannot_be_blocked|cannot_be_destroyed|win_battle|break_shield|look_at_top|shuffle|cost_reduce|cost_increase|give_keyword|banish_to_abyss|move_zone|reveal|GR_summon|copy_effect|none>",
  "effect_target": "<JSON object as a string, or null>",
  "effect_value": "<JSON value as a string, or null>",
  "is_optional": <boolean>,
  "is_replacement": <boolean>,
  "active_in_phase": <array of strings, or ["any"]>,
  "active_in_zone": <array of strings, or ["battle_zone"]>,
  "parse_confidence": <float 0.0-1.0>
}

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
            else:
                response = client.chat.send(model=model, messages=messages)
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
        except (RuntimeError, TimeoutError, ConnectionError) as e:
            logger.error(f"API error for {card_name}: {e}")
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
        except (RuntimeError, TimeoutError, ConnectionError) as e:
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
                   COALESCE(
                       ARRAY(SELECT raw_text FROM card_effects
                             WHERE card_id=c.id ORDER BY ability_index),
                       '{}'
                   ) AS existing_effects,
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


def _fetch_unparsed_cards_by_slugs(conn, slugs: list[str], limit: int | None = None) -> list[dict]:
    """Get requested cards that do not have parsed effects yet."""
    if not slugs:
        return []

    unique_slugs = list(dict.fromkeys(slugs))
    sql = """
        SELECT c.id, c.name, c.card_type, c.slug, c.raw_text,
               COALESCE(
                   ARRAY(SELECT raw_text FROM card_effects
                         WHERE card_id=c.id ORDER BY ability_index),
                   '{}'
               ) AS existing_effects,
               cu.url
        FROM cards c
        JOIN card_urls cu ON cu.url = c.source_url
        WHERE c.slug = ANY(%s)
          AND NOT EXISTS (
              SELECT 1 FROM card_effects ce WHERE ce.card_id = c.id
          )
        ORDER BY array_position(%s::text[], c.slug)
    """
    params: list[Any] = [unique_slugs, unique_slugs]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


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

def parse_pending_cards(
    dsn: str,
    api_key: str | None,
    batch_size: int = 50,
    cards_per_call: int = 2,
    delay_between: float = 0.5,
    model: str = "nvidia/nemotron-3-super-120b-a12b:free",
    base_url: str | None = None,
    provider: str = "openrouter",
    ollama_host: str = "http://localhost:11434",
    rules_context_config: RulesContextConfig | None = None,
    retries: int = 5,
    max_tokens: int = 2048,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict:
    """
    Parse all cards with status='scraped' using OpenAI.

    Args:
        dsn:             PostgreSQL DSN
        api_key:         API key, needed for OpenRouter/OpenAI
        batch_size:      How many cards to process per run
        cards_per_call:  How many cards to parse in one OpenRouter request
        delay_between:   Seconds to sleep between API calls
        model:           OpenRouter/Ollama model name
        base_url:        Deprecated; ignored when using the OpenRouter SDK
        provider:        "openrouter", "openai", or "ollama"
        ollama_host:     Ollama server URL when provider is "ollama"
        retries:         LLM retries per card for retryable provider errors
        max_tokens:      Maximum output tokens requested from OpenRouter

    Returns:
        {"parsed": N, "errors": M, "skipped": K}
    """
    if provider not in ("openrouter", "openai", "ollama"):
        raise ValueError("provider must be 'openrouter', 'openai', or 'ollama'")
    if provider in ("openrouter", "openai") and (not api_key or not api_key.strip()):
        raise ValueError("Missing LLM API key. Set OPENROUTER_API_KEY/OPENAI_API_KEY or pass --api-key.")
    if cards_per_call < 1:
        raise ValueError("cards_per_call must be at least 1")

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
        else:
            client_context = _NullLlmClient()
        with client_context as client:
            cards = _fetch_pending_cards(conn, batch_size)
            logger.info(f"Found {len(cards)} scraped cards pending LLM parse")

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
                    counts[key] += value

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
    cards_per_call: int = 2,
    delay_between: float = 0.5,
    model: str = "nvidia/nemotron-3-super-120b-a12b:free",
    provider: str = "openrouter",
    ollama_host: str = "http://localhost:11434",
    rules_context_config: RulesContextConfig | None = None,
    retries: int = 5,
    max_tokens: int = 2048,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict:
    """Parse only the requested card slugs that have no card_effects rows yet."""
    if provider not in ("openrouter", "openai", "ollama"):
        raise ValueError("provider must be 'openrouter', 'openai', or 'ollama'")
    if provider in ("openrouter", "openai") and (not api_key or not api_key.strip()):
        raise ValueError("Missing LLM API key. Set OPENROUTER_API_KEY/OPENAI_API_KEY or pass --api-key.")
    if cards_per_call < 1:
        raise ValueError("cards_per_call must be at least 1")

    conn = psycopg2.connect(dsn)
    counts = {
        "parsed": 0,
        "errors": 0,
        "skipped": 0,
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
        else:
            client_context = _NullLlmClient()

        with client_context as client:
            cards = _fetch_unparsed_cards_by_slugs(conn, slugs)
            counts["already_parsed"] = counts["requested"] - len(cards)
            logger.info(
                "Found %s requested deck cards without parsed effects (%s already parsed)",
                len(cards),
                counts["already_parsed"],
            )

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
