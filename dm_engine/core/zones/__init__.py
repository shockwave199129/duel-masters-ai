"""core/zones — Zone objects that hold cards during a game.

These are the STATEFUL wrappers around CardDefinitions.
A CardDefinition never changes. A zone object tracks everything
that changes during play: tapped status, power modifications,
which shields are revealed, etc.
"""

from .cards import _new_uid, HandCard, HyperspatialCard, ManaCard, ShieldCard, GraveyardCard
from .creature import PowerModifier, EvolutionStackEntry, Creature
