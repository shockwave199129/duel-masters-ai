"""
decks/prebuilt.py — reusable deck definitions for simulations.

Bots receive legal actions from a GameState. To give bots decks, build two
DeckDefinitions here, initialize a GameState, then run the simulation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.cards import CardDefinition, DeckDefinition
from core.enums import CardSubtype, CardType, Civilization, Keyword
from core.initializer import initialize_game
from core.state import GameState
from core.zones import Creature


@dataclass(frozen=True)
class PrebuiltDeckSpec:
    """
    A full pregame setup for one player.

    Rules:
    - 100.2: main deck is exactly 40 cards.
    - 100.3: Hyperspatial Zone is separate from deck, up to 8 cards.
    - 100.4: Ultra GR Zone is separate from deck, exactly 12 cards if used.
    - 100.5: only one starting Battle Zone set may exist at game start.
    """
    main_deck: DeckDefinition
    hyperspatial: tuple[CardDefinition, ...] = tuple()
    ultra_gr: tuple[CardDefinition, ...] = tuple()
    start_battle_zone: tuple[CardDefinition, ...] = tuple()


def make_demo_decks() -> tuple[DeckDefinition, DeckDefinition]:
    """
    Return two valid, fully resolved 40-card decks for engine simulations.

    These demo decks do not need the card database. They are intentionally
    simple so bot/game-loop tests can run before real card effects are complete.
    """
    return (
        make_demo_deck("demo_fire_attack", "Player 0", Civilization.FIRE, 1000),
        make_demo_deck("demo_water_blocker", "Player 1", Civilization.WATER, 2000),
    )


def make_demo_deck(
    name: str,
    owner: str,
    civilization: Civilization,
    first_card_id: int,
) -> DeckDefinition:
    """Build one 40-card demo deck from 10 unique cards with 4 copies each."""
    card_definitions: dict[int, CardDefinition] = {}
    card_counts: dict[int, int] = {}

    for offset in range(10):
        card_id = first_card_id + offset
        keywords = frozenset([Keyword.BLOCKER]) if civilization == Civilization.WATER and offset < 3 else frozenset()
        defn = _vanilla_creature(
            card_id=card_id,
            name=f"{name}_{offset + 1}",
            civilization=civilization,
            power=1000 + (offset * 1000),
            keywords=keywords,
        )
        card_definitions[card_id] = defn
        card_counts[card_id] = 4

    return DeckDefinition(
        name=name,
        owner=owner,
        card_counts=card_counts,
        card_definitions=card_definitions,
    )


def build_deck_from_names(
    db,
    name: str,
    owner: str,
    cards: dict[str, int],
) -> DeckDefinition:
    """
    Build a real deck from card names or slugs in CardDatabase.

    Example:
        deck = build_deck_from_names(db, "My Deck", "Player 0", {
            "Bolshack Dragon": 4,
            "Aqua Hulcus": 4,
            # ...
        })
    """
    return db.build_deck(name=name, owner=owner, cards=cards)


def load_prebuilt_deck_json(path: str | Path, db) -> PrebuiltDeckSpec:
    """
    Load one player's prebuilt deck from JSON.

    Supported JSON shape:
        {
          "name": "Fire Rush",
          "owner": "Player 0",
          "main": {"bolshack-dragon": 4, "aqua-hulcus": 4},
          "hyperspatial": {"psychic-creature-slug": 1},
          "ultra_gr": {"gr-creature-slug": 2},
          "start_battle_zone": ["forbidden-sealed-x"]
        }

    Keys may be card slugs, card names, or numeric card IDs as strings.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return prebuilt_deck_from_dict(data, db)


def load_prebuilt_game_json(
    path: str | Path,
    db,
    *,
    first_player: int | None = None,
    seed: int | None = None,
    game_id: str = "",
) -> GameState:
    """
    Load two player deck specs from JSON and create a GameState.

    JSON shape:
        {
          "players": [
            {"name": "Deck A", "owner": "Player 0", "main": {...}},
            {"name": "Deck B", "owner": "Player 1", "main": {...}}
          ]
        }
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    players = data.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError("Prebuilt game JSON must contain exactly two players")

    p0 = prebuilt_deck_from_dict(players[0], db)
    p1 = prebuilt_deck_from_dict(players[1], db)
    state = initialize_game(
        p0.main_deck,
        p1.main_deck,
        first_player=first_player,
        seed=seed,
        game_id=game_id,
    )
    _apply_extra_zones(state, 0, p0)
    _apply_extra_zones(state, 1, p1)
    return state


def prebuilt_deck_from_dict(data: dict[str, Any], db) -> PrebuiltDeckSpec:
    """Build a PrebuiltDeckSpec from parsed JSON data."""
    name = str(data.get("name", "Prebuilt Deck"))
    owner = str(data.get("owner", "Player"))
    main_cards = _read_card_counts(data.get("main", data.get("cards", {})), "main")
    main_deck = build_deck_from_names(db, name, owner, main_cards)

    hyperspatial = _expand_zone_cards(db, data.get("hyperspatial", {}), "hyperspatial")
    ultra_gr = _expand_zone_cards(db, data.get("ultra_gr", {}), "ultra_gr")
    start_battle_zone = _expand_zone_cards(db, data.get("start_battle_zone", []), "start_battle_zone")

    _validate_extra_zones(hyperspatial, ultra_gr, start_battle_zone)
    return PrebuiltDeckSpec(
        main_deck=main_deck,
        hyperspatial=tuple(hyperspatial),
        ultra_gr=tuple(ultra_gr),
        start_battle_zone=tuple(start_battle_zone),
    )


def _vanilla_creature(
    card_id: int,
    name: str,
    civilization: Civilization,
    power: int,
    keywords: frozenset[Keyword] = frozenset(),
) -> CardDefinition:
    return CardDefinition(
        id=card_id,
        slug=name.lower(),
        name=name,
        cost=1,
        power=power,
        card_type=CardType.CREATURE,
        card_subtype=CardSubtype.NONE,
        civilizations=frozenset([civilization]),
        races=frozenset(),
        keywords=keywords,
        effects=tuple(),
        evolution_source_races=frozenset(),
        evolution_source_types=frozenset(),
        is_multiface=False,
    )


def _read_card_counts(value: Any, zone_name: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"'{zone_name}' must be an object of card key to count")
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(count, int) or count <= 0:
            raise ValueError(f"'{zone_name}' card '{key}' must have a positive integer count")
        result[str(key)] = count
    return result


def _expand_zone_cards(db, value: Any, zone_name: str) -> list[CardDefinition]:
    if value in (None, {}, []):
        return []

    cards: list[CardDefinition] = []
    if isinstance(value, dict):
        counts = _read_card_counts(value, zone_name)
        for card_key, count in counts.items():
            cards.extend([_resolve_card(db, card_key)] * count)
        return cards

    if isinstance(value, list):
        for card_key in value:
            cards.append(_resolve_card(db, str(card_key)))
        return cards

    raise ValueError(f"'{zone_name}' must be an object or list")


def _resolve_card(db, card_key: str) -> CardDefinition:
    if card_key.isdigit():
        return db.require(int(card_key))

    defn = db.get_by_slug(card_key)
    if defn is not None:
        return defn

    lower_key = card_key.lower()
    for candidate in db.all_cards():
        if candidate.name.lower() == lower_key:
            return candidate
    raise ValueError(f"Card not found: '{card_key}'")


def _validate_extra_zones(
    hyperspatial: list[CardDefinition],
    ultra_gr: list[CardDefinition],
    start_battle_zone: list[CardDefinition],
) -> None:
    if len(hyperspatial) > 8:
        raise ValueError("Hyperspatial Zone can contain up to 8 cards")
    _validate_copy_limit(hyperspatial, 4, "Hyperspatial Zone")

    if ultra_gr and len(ultra_gr) != 12:
        raise ValueError("Ultra GR Zone must contain exactly 12 cards when used")
    _validate_copy_limit(ultra_gr, 2, "Ultra GR Zone")

    if len(start_battle_zone) > 1:
        raise ValueError("Only one starting Battle Zone set is currently supported")


def _validate_copy_limit(cards: list[CardDefinition], limit: int, zone_name: str) -> None:
    counts: dict[int, int] = {}
    for defn in cards:
        counts[defn.id] = counts.get(defn.id, 0) + 1
    over_limit = [defn_id for defn_id, count in counts.items() if count > limit]
    if over_limit:
        raise ValueError(f"{zone_name} exceeds copy limit for card IDs: {over_limit}")


def _apply_extra_zones(state: GameState, player: int, spec: PrebuiltDeckSpec) -> None:
    p_state = state.players[player]
    p_state.hyperspatial_zone = [
        Creature(definition=defn, controller=player, owner=player, has_summoning_sickness=False)
        for defn in spec.hyperspatial
    ]
    p_state.ultra_gr_zone = list(spec.ultra_gr)
    p_state.battle_zone.extend(
        Creature(
            definition=defn,
            controller=player,
            owner=player,
            entered_turn=state.turn_number,
            has_summoning_sickness=False,
        )
        for defn in spec.start_battle_zone
    )
