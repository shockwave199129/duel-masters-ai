"""
engine/layers.py — Layer system for continuous effects (MTG rule 613 inspired).

Provides a deterministic ordering for applying continuous effects:

  Layer 1 — Copy effects
  Layer 2 — Control-changing effects
  Layer 3 — Text-changing effects
  Layer 4 — Type-changing effects
  Layer 5 — Color-changing effects
  Layer 6 — Ability-adding/removing effects
  Layer 7 — Power/toughness modifiers

Within each layer, effects are ordered by timestamp (most recent first).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

sys.path.insert(0, "dm_engine")

from core.global_effects import GlobalEffect


class Layer(Enum):
    """The seven layers for applying continuous effects, matching MTG rule 613."""
    COPY            = 1
    CONTROL         = 2
    TEXT            = 3
    TYPE            = 4
    COLOR           = 5
    ABILITY         = 6
    POWER_TOUGHNESS = 7


@dataclass
class LayeredEffect:
    """A GlobalEffect wrapped with layer and ordering metadata."""
    layer:     Layer
    timestamp: int
    effect:    GlobalEffect
    depends_on: Optional[str] = None


@dataclass
class LayerEffectRegistry:
    """Manages all layered continuous effects, sorted by (layer ASC, timestamp DESC)."""

    _effects: list[LayeredEffect] = field(default_factory=list)
    _timestamp_counter: int = 0

    def add(
        self,
        effect: GlobalEffect,
        layer: Layer,
        depends_on: Optional[str] = None,
    ) -> LayeredEffect:
        """Wrap a GlobalEffect in the given layer and insert in sorted order."""
        self._timestamp_counter += 1
        layered = LayeredEffect(
            layer=layer,
            timestamp=self._timestamp_counter,
            effect=effect,
            depends_on=depends_on,
        )
        self._effects.append(layered)
        self._effects.sort(key=lambda e: (e.layer.value, -e.timestamp))
        return layered

    def remove_by_source(self, source_uid: str) -> int:
        """Remove all effects originating from the given source uid. Returns count removed."""
        before = len(self._effects)
        self._effects = [e for e in self._effects if e.effect.applied_by_uid != source_uid]
        return before - len(self._effects)

    def get_effects_for_layer(self, layer: Layer) -> list[LayeredEffect]:
        """Return effects in the given layer, sorted by timestamp (most recent first)."""
        return [e for e in self._effects if e.layer == layer]

    def get_effects_in_order(self) -> list[LayeredEffect]:
        """Return all effects in layer order (1→7), then by timestamp within each layer."""
        return list(self._effects)  # already kept sorted by (layer ASC, timestamp DESC)

    def get_layer_power_modifiers(self, player: int, controller: int) -> list[LayeredEffect]:
        """Return POWER_TOUGHNESS layer effects matching the given player/controller."""
        results: list[LayeredEffect] = []
        for e in self._effects:
            if e.layer != Layer.POWER_TOUGHNESS:
                continue
            eff = e.effect
            if eff.controller != controller:
                continue
            if eff.target_player is not None and eff.target_player != player:
                continue
            results.append(e)
        return results

    def get_layer_keywords(self, player: int) -> list[LayeredEffect]:
        """Return ABILITY layer keyword-grant effects for the given player."""
        results: list[LayeredEffect] = []
        for e in self._effects:
            if e.layer != Layer.ABILITY:
                continue
            eff = e.effect
            if eff.target_player is not None and eff.target_player != player:
                continue
            results.append(e)
        return results

    def reset(self) -> None:
        """Clear all layered effects."""
        self._effects.clear()
        self._timestamp_counter = 0
