"""Hybrid rules access for deterministic gameplay features and diagnostics.

The engine still owns legality and execution. This module exposes structured
rules data for feature engineering and uses semantic retrieval only for
explanations/debugging.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from core.enums import Phase

logger = logging.getLogger(__name__)


ConnectFactory = Callable[[str], Any]


@dataclass(frozen=True)
class RuleFact:
    rule_number: str
    text: str
    rule_category: str = "general"
    applies_in_phase: tuple[str, ...] = ("any",)
    applies_in_zone: tuple[str, ...] = ("any",)
    is_state_based: bool = False
    is_turn_based: bool = False
    is_keyword_rule: bool = False
    priority: int = 100


@dataclass(frozen=True)
class PhaseInfo:
    phase_key: str
    phase_name: str
    phase_order: int
    is_optional: bool
    can_repeat: bool
    rule_ref: str = ""
    description: str = ""


@dataclass(frozen=True)
class KeywordRule:
    name: str
    short_desc: str = ""
    full_rule_ref: str = ""
    overrides_summoning_sickness: bool = False
    is_triggered: bool = False
    is_activated: bool = False
    is_static: bool = False
    is_replacement: bool = False
    requires_declaration: bool = False
    usable_in_phase: tuple[str, ...] = ()


@dataclass(frozen=True)
class StateBasedActionFact:
    rule_number: str
    action_key: str
    description: str
    condition_json: dict[str, Any]
    effect_json: dict[str, Any]
    priority: int = 100


@dataclass(frozen=True)
class SemanticRule:
    rule_number: str
    text: str
    score: float = 0.0
    metadata: dict[str, Any] | None = None


_PHASE_KEY_BY_ENGINE_PHASE = {
    Phase.START_OF_TURN: "turn_start",
    Phase.DRAW: "draw",
    Phase.MANA_CHARGE: "mana_charge",
    Phase.MAIN: "main",
    Phase.ATTACK: "attack",
    Phase.ATTACK_DECLARE: "attack_declare",
    Phase.BLOCK_DECLARE: "block_declare",
    Phase.BATTLE: "battle",
    Phase.DIRECT_ATTACK: "direct_attack",
    Phase.END_OF_ATTACK: "attack_end",
    Phase.END_OF_TURN: "turn_end",
}


def phase_key_for_engine_phase(phase: Phase) -> str:
    return _PHASE_KEY_BY_ENGINE_PHASE.get(phase, phase.name.lower())


def _tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class RuleKnowledgeService:
    """Read structured rules from Postgres and optional semantic rules from Chroma."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        chroma_path: str | None = None,
        embedding_key: str | None = None,
        connect_factory: ConnectFactory | None = None,
        semantic_retriever: Any | None = None,
    ):
        self.dsn = dsn
        self.chroma_path = chroma_path
        self.embedding_key = embedding_key
        self._connect_factory = connect_factory
        self._semantic_retriever = semantic_retriever
        self._rule_cache: dict[str, RuleFact | None] = {}
        self._phase_cache: dict[str, PhaseInfo | None] = {}
        self._keyword_cache: dict[str, KeywordRule | None] = {}
        self._sba_cache: list[StateBasedActionFact] | None = None

    @classmethod
    def from_card_database(
        cls,
        db,
        *,
        chroma_path: str | None = None,
        embedding_key: str | None = None,
    ) -> "RuleKnowledgeService":
        return cls(
            getattr(db, "_dsn", None),
            chroma_path=chroma_path,
            embedding_key=embedding_key,
        )

    def is_configured(self) -> bool:
        return bool(self.dsn)

    def get_rule(self, rule_number: str) -> RuleFact | None:
        if rule_number not in self._rule_cache:
            rows = self.get_rules([rule_number])
            self._rule_cache[rule_number] = rows.get(rule_number)
        return self._rule_cache[rule_number]

    def get_rules(self, rule_numbers: Iterable[str]) -> dict[str, RuleFact]:
        numbers = [str(number) for number in rule_numbers]
        missing = [number for number in numbers if number not in self._rule_cache]
        if missing and self.dsn:
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT rule_number, text, rule_category, applies_in_phase,
                                   applies_in_zone, is_state_based, is_turn_based,
                                   is_keyword_rule, priority
                            FROM dm_rules
                            WHERE rule_number = ANY(%s)
                            """,
                            (missing,),
                        )
                        for row in cur.fetchall():
                            fact = self._rule_from_row(row)
                            self._rule_cache[fact.rule_number] = fact
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Could not query dm_rules: %s", exc)
        for number in missing:
            self._rule_cache.setdefault(number, None)
        return {
            number: fact
            for number in numbers
            if (fact := self._rule_cache.get(number)) is not None
        }

    def get_phase_info(self, phase: Phase | str) -> PhaseInfo | None:
        phase_key = phase_key_for_engine_phase(phase) if isinstance(phase, Phase) else phase
        if phase_key in self._phase_cache:
            return self._phase_cache[phase_key]
        info = None
        if self.dsn:
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT phase_key, phase_name, phase_order, is_optional,
                                   can_repeat, rule_ref, description
                            FROM dm_game_phases
                            WHERE phase_key = %s
                            """,
                            (phase_key,),
                        )
                        row = cur.fetchone()
                        if row:
                            info = self._phase_from_row(row)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Could not query dm_game_phases: %s", exc)
        self._phase_cache[phase_key] = info
        return info

    def get_phase_rules(self, phase: Phase | str) -> list[RuleFact]:
        phase_key = phase_key_for_engine_phase(phase) if isinstance(phase, Phase) else phase
        if not self.dsn:
            return []
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT rule_number, text, rule_category, applies_in_phase,
                               applies_in_zone, is_state_based, is_turn_based,
                               is_keyword_rule, priority
                        FROM dm_rules
                        WHERE %s = ANY(applies_in_phase)
                           OR 'any' = ANY(applies_in_phase)
                        ORDER BY priority, rule_number
                        """,
                        (phase_key,),
                    )
                    return [self._rule_from_row(row) for row in cur.fetchall()]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Could not query phase rules: %s", exc)
            return []

    def get_keyword_rule(self, keyword_name: str) -> KeywordRule | None:
        key = keyword_name.lower().replace("_", " ")
        if key in self._keyword_cache:
            return self._keyword_cache[key]
        rule = None
        if self.dsn:
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT name, short_desc, full_rule_ref,
                                   overrides_summoning_sickness, is_triggered,
                                   is_activated, is_static, is_replacement,
                                   requires_declaration, usable_in_phase
                            FROM dm_keywords
                            WHERE lower(name) IN (%s, %s)
                            """,
                            (key, keyword_name.lower()),
                        )
                        row = cur.fetchone()
                        if row:
                            rule = self._keyword_from_row(row)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Could not query dm_keywords: %s", exc)
        self._keyword_cache[key] = rule
        return rule

    def get_keyword_rules(self, keyword_names: Iterable[str]) -> dict[str, KeywordRule]:
        result: dict[str, KeywordRule] = {}
        for name in keyword_names:
            if rule := self.get_keyword_rule(str(name)):
                result[str(name)] = rule
        return result

    def get_state_based_actions(self) -> list[StateBasedActionFact]:
        if self._sba_cache is not None:
            return list(self._sba_cache)
        actions: list[StateBasedActionFact] = []
        if self.dsn:
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT rule_number, action_key, description,
                                   condition_json, effect_json, priority
                            FROM dm_state_based_actions
                            ORDER BY priority, action_key
                            """
                        )
                        actions = [self._sba_from_row(row) for row in cur.fetchall()]
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Could not query dm_state_based_actions: %s", exc)
        self._sba_cache = actions
        return list(actions)

    def search_semantic_rules(
        self,
        query: str,
        *,
        n: int = 5,
        phase: str | None = None,
        zone: str | None = None,
        category: str | None = None,
    ) -> list[SemanticRule]:
        retriever = self._get_semantic_retriever()
        if retriever is None:
            return []
        try:
            rows = retriever.search(query, n=n, phase=phase, zone=zone, category=category)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Could not search semantic rules: %s", exc)
            return []
        return [
            SemanticRule(
                rule_number=str(row.get("rule_number", "")),
                text=str(row.get("text", "")),
                score=float(row.get("score", 0.0) or 0.0),
                metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            )
            for row in rows
        ]

    def build_context_for_event(
        self,
        event_description: str,
        current_phase: Phase | str,
        *,
        n: int = 8,
    ) -> str:
        phase_key = phase_key_for_engine_phase(current_phase) if isinstance(current_phase, Phase) else current_phase
        rules = self.search_semantic_rules(event_description, n=n, phase=phase_key)
        if not rules:
            return ""
        return "\n\n".join(f"[{rule.rule_number}] {rule.text}" for rule in rules)

    def query_card_rulings(
        self,
        card_name: str,
        ability_text: str,
        *,
        n: int = 3,
        chroma_path: str | None = None,
        openai_key: str | None = None,
    ) -> list[dict]:
        """
        Query ChromaDB for card-specific rulings.

        This searches a separate 'card_rulings' collection (if it exists)
        for rulings related to a specific card and ability.

        Args:
            card_name: Name of the card (e.g., "Bolshack Dragon")
            ability_text: The ability text to search for
            n: Number of results to return
            chroma_path: Override ChromaDB path (defaults to self.chroma_path)
            openai_key: Override OpenAI key (defaults to self.embedding_key)

        Returns:
            List of dicts with keys: text, score, metadata
        """
        path = chroma_path or self.chroma_path
        key = openai_key or self.embedding_key

        if not path:
            return []

        try:
            from rules_ingest.ingest_chroma import _get_embedding_function
            import chromadb
            ef = _get_embedding_function(key)
            client = chromadb.PersistentClient(path=path)
            collection = client.get_collection(
                name="card_rulings",
                embedding_function=ef,
            )
        except Exception:
            # Collection doesn't exist or ChromaDB not available
            return []

        query = f"{card_name}: {ability_text}"
        try:
            results = collection.query(
                query_texts=[query],
                n_results=n,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            logger.warning("Could not query card_rulings: %s", exc)
            return []

        output = []
        for i in range(len(results["ids"][0])):
            output.append({
                "text": results["documents"][0][i],
                "score": 1 - results["distances"][0][i],
                "metadata": results["metadatas"][0][i],
            })
        return output

    def _connect(self):
        if not self.dsn:
            raise RuntimeError("RuleKnowledgeService requires a PostgreSQL DSN")
        if self._connect_factory is not None:
            return self._connect_factory(self.dsn)
        import psycopg2

        return psycopg2.connect(self.dsn)

    def _get_semantic_retriever(self):
        if self._semantic_retriever is not None:
            return self._semantic_retriever
        if not self.chroma_path:
            return None
        try:
            from rules_ingest.ingest_chroma import DMRulesRetriever

            self._semantic_retriever = DMRulesRetriever(self.chroma_path, self.embedding_key)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Chroma rule retrieval unavailable: %s", exc)
            self._semantic_retriever = None
        return self._semantic_retriever

    @staticmethod
    def _rule_from_row(row) -> RuleFact:
        return RuleFact(
            rule_number=str(row[0]),
            text=str(row[1] or ""),
            rule_category=str(row[2] or "general"),
            applies_in_phase=_tuple(row[3] or ("any",)),
            applies_in_zone=_tuple(row[4] or ("any",)),
            is_state_based=bool(row[5]),
            is_turn_based=bool(row[6]),
            is_keyword_rule=bool(row[7]),
            priority=int(row[8] or 100),
        )

    @staticmethod
    def _phase_from_row(row) -> PhaseInfo:
        return PhaseInfo(
            phase_key=str(row[0]),
            phase_name=str(row[1] or ""),
            phase_order=int(row[2] or 0),
            is_optional=bool(row[3]),
            can_repeat=bool(row[4]),
            rule_ref=str(row[5] or ""),
            description=str(row[6] or ""),
        )

    @staticmethod
    def _keyword_from_row(row) -> KeywordRule:
        return KeywordRule(
            name=str(row[0]),
            short_desc=str(row[1] or ""),
            full_rule_ref=str(row[2] or ""),
            overrides_summoning_sickness=bool(row[3]),
            is_triggered=bool(row[4]),
            is_activated=bool(row[5]),
            is_static=bool(row[6]),
            is_replacement=bool(row[7]),
            requires_declaration=bool(row[8]),
            usable_in_phase=_tuple(row[9]),
        )

    @staticmethod
    def _sba_from_row(row) -> StateBasedActionFact:
        return StateBasedActionFact(
            rule_number=str(row[0]),
            action_key=str(row[1]),
            description=str(row[2] or ""),
            condition_json=_dict(row[3]),
            effect_json=_dict(row[4]),
            priority=int(row[5] or 100),
        )
