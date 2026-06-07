"""
core/enums.py — All enums and constants for the DM game engine.

Updated after full read of Duel Masters Comprehensive Game Rules Ver. 1.50.
Rule references are cited inline (e.g. rule 501, rule 112.2a).
"""

from enum import Enum, auto


# ── Civilizations ─────────────────────────────────────────────────────────────

class Civilization(Enum):
    FIRE     = "Fire"
    WATER    = "Water"
    NATURE   = "Nature"
    LIGHT    = "Light"
    DARKNESS = "Darkness"

    def __str__(self) -> str:
        return self.value


# ── Card Types ────────────────────────────────────────────────────────────────
# Full list from rule 204.2

class CardType(Enum):
    CREATURE    = "Creature"
    SPELL       = "Spell"
    CROSS_GEAR  = "Cross Gear"
    CASTLE      = "Castle"
    CELL        = "Cell"          # component of combined creatures (rule 204.2)
    WEAPON      = "Weapon"        # Dragheart Weapon (rule 305)
    FORTRESS    = "Fortress"      # Dragheart Fortress (rule 306)
    HEARTBEAT   = "Heartbeat"     # Kodo — Forbidden double-sided card (rule 307)
    FIELD       = "Field"         # D2 Field and others (rule 308)
    CORE        = "Core"          # Forbidden Core (rule 309)
    AURA        = "Aura"          # attaches to creatures
    RITUAL      = "Ritual"        # Gi
    NEBULA      = "Nebula"        # Seiun
    ARTIFACT    = "Artifact"
    LAND        = "Land"
    RULE_PLUS   = "Rule Plus"
    TAMASEED    = "Tamaseed"      # DMRP-21+ (rule 204.2)
    DUELIST     = "Duelist"       # rule 204.2

    def __str__(self) -> str:
        return self.value


class CardSubtype(Enum):
    """Supertypes (special types) that modify how a card is played. Rule 204.3."""
    NONE              = "None"
    EVOLUTION         = "Evolution"        # rule 801
    NEO_EVOLUTION     = "Neo Evolution"    # rule 802
    G_NEO             = "G-NEO"            # rule 803
    SUPER_EVOLUTION   = "Super Evolution"
    PSYCHIC           = "Psychic"          # rule 805
    PSYCHIC_SUPER     = "Psychic Super"    # rule 805.1c
    EXILE             = "Exile"            # rule 204.3a
    DRAGHEART         = "Dragheart"        # rule 204.3a
    FORBIDDEN         = "Forbidden"        # rule 204.3
    FINAL_FORBIDDEN   = "Final Forbidden"  # rule 204.3c
    D2                = "D2"               # D2 Field (rule 204.3c)
    GR                = "GR"               # GR creature (rule 204.3a)
    STAR_MAX          = "Star Max Evolution" # rule 815
    GACHARANGE        = "Gacharange"
    NEO               = "NEO"              # NEO Creature (rule 802)
    DREAM             = "Dream"            # Dream Rare (rule 817)


# ── Zones ─────────────────────────────────────────────────────────────────────
# All possible zones a card can be in. Rules 400–410.

class Zone(Enum):
    DECK          = "deck"
    HAND          = "hand"
    MANA_ZONE     = "mana_zone"
    BATTLE_ZONE   = "battle_zone"
    SHIELD_ZONE   = "shield_zone"
    GRAVEYARD     = "graveyard"
    ABYSS_ZONE    = "abyss_zone"      # rule 410 — banished, never returns
    HYPERSPATIAL  = "hyperspatial"    # rule 407 — Psychic/Dragheart/Duel Mate
    ULTRA_GR      = "ultra_gr"        # rule 408 — GR creatures, face-down
    PENDING       = "pending"         # rule 409 — between zones during execution

    def __str__(self) -> str:
        return self.value


# ── Game Phases / Steps ───────────────────────────────────────────────────────
# Rule 500.1: "A turn consists of 6 steps: Start of Turn, Draw, Mana Charge,
# Main, Attack, and End of Turn."
# Rule 505.1: "The Attack Step consists of 5 sub-steps: Specify Attacking Creature,
# Specify Blocking Creature, Battle, Direct Attack, and End of Attack."

class Phase(Enum):
    # ── Main turn steps (rule 500.1) ──────────────────────────────────────────
    START_OF_TURN  = 0   # rule 501: untap + start-of-turn triggers (turn-based action)
    DRAW           = 1   # rule 502: draw 1 card (skipped for first player turn 1)
    MANA_CHARGE    = 2   # rule 503: optional — place 1 card from hand to mana
    MAIN           = 3   # rule 504: summon creatures, cast spells, deploy fields, etc.
    ATTACK         = 4   # rule 505: outer attack step (enter to start choosing attackers)
    END_OF_TURN    = 5   # rule 500: end-of-turn triggers, effect expiry

    # ── Attack sub-steps (rule 505.1) ─────────────────────────────────────────
    # These are sub-phases within ATTACK.
    ATTACK_DECLARE = 10  # rule 506: turn player specifies attacking creature
    BLOCK_DECLARE  = 11  # rule 507: non-turn player may specify blocking creature
    BATTLE         = 12  # rule 508: battle if attack target was changed (blocked)
    DIRECT_ATTACK  = 13  # rule 509: break shields or direct attack if unblocked
    END_OF_ATTACK  = 14  # rule 505.1: end of this individual attack

    def __str__(self) -> str:
        return self.name

    def is_attack_subphase(self) -> bool:
        return self.value >= 10


# ── Keywords ──────────────────────────────────────────────────────────────────
# Sourced from dm_keywords table + rules sections 112.3, 701, 816.

class Keyword(Enum):
    # ── Breaker abilities (rule 509.2) ────────────────────────────────────────
    DOUBLE_BREAKER          = "double_breaker"
    TRIPLE_BREAKER          = "triple_breaker"
    WORLD_BREAKER           = "world_breaker"

    # ── Evasion / attack modification ─────────────────────────────────────────
    SPEED_ATTACKER          = "speed_attacker"      # no summoning sickness (rule 301.5)
    CANNOT_BE_BLOCKED       = "cannot_be_blocked"
    CANNOT_ATTACK           = "cannot_attack"
    CANNOT_ATTACK_PLAYERS   = "cannot_attack_players"
    MACH_FIGHTER            = "mach_fighter"        # can attack tapped creatures

    # ── Defense ───────────────────────────────────────────────────────────────
    BLOCKER                 = "blocker"             # rule 701.12 — intercepts attacks
    GUARDMAN                = "guardman"            # must block if conditions met
    SLAYER                  = "slayer"              # destroys anything it battles

    # ── Free execution abilities (rule 112.3) ─────────────────────────────────
    SHIELD_TRIGGER          = "shield_trigger"      # rule 112.3a — cast for free from broken shield
    S_BACK                  = "s_back"              # rule 112.3b — discard specified card
    NINJA_STRIKE            = "ninja_strike"        # rule 112.3c — summon during attack window
    SABAKI_Z                = "sabaki_z"            # rule 112.3d — discard Emblem of Judgment
    G_ZERO                  = "g_zero"              # rule 112.3e — free if condition met
    GRAVITY_ZERO            = "gravity_zero"        # alias for G_ZERO (older name)
    ATTACK_CHANCE           = "attack_chance"       # rule 112.3f — cast spell for free on attack
    MADNESS                 = "madness"             # cast for free when discarded
    G_STRIKE                = "g_strike"            # rule 101.4b — same timing as S-Trigger
    KIRIFUDASH              = "kirifudash"          # attack-based condition ability

    # ── Power modification ────────────────────────────────────────────────────
    POWER_ATTACKER          = "power_attacker"      # +N power when attacking
    SYMPATHY                = "sympathy"            # cost reduced per matching creature

    # ── Special summoning ─────────────────────────────────────────────────────
    INVASION                = "invasion"            # summon on top when condition met
    REVOLUTION_CHANGE       = "revolution_change"   # swap with creature when attacking (rule 701.26)

    # ── Start-of-turn ─────────────────────────────────────────────────────────
    SILENT_SKILL            = "silent_skill"        # rule 501.1a — may choose not to untap

    # ── Zone-specific abilities ───────────────────────────────────────────────
    MANA_BURST              = "mana_burst"          # rule 110.4b — functions from mana zone
    JUST_DIVER              = "just_diver"          # put into opponent's mana zone on ETB

    # ── Hyper Mode (rule 816) ─────────────────────────────────────────────────
    HYPERIZE                = "hyperize"            # activate Hyper Mode release

    # ── Other ─────────────────────────────────────────────────────────────────
    VEIL                    = "veil"
    FORTRESS_KEYWORD        = "fortress"
    EX_LIFE                 = "ex_life"             # shield a card when entering

    def __str__(self) -> str:
        return self.value


# ── Action Types ──────────────────────────────────────────────────────────────
# Every possible action a player can take, derived from rules.

class ActionType(Enum):
    # ── Main phase — execute cards (rule 504.1) ───────────────────────────────
    SUMMON_CREATURE     = "summon_creature"     # rule 301.1 / 701.3
    CAST_SPELL          = "cast_spell"          # rule 302.1 / 701.4
    GENERATE_CROSS_GEAR = "generate_cross_gear" # rule 303.1 / 701.16
    CROSS_GEAR          = "cross_gear"          # rule 303.3b — cross existing gear onto creature
    FORTIFY_CASTLE      = "fortify_castle"      # rule 304.1 / 701.19
    DEPLOY_FIELD        = "deploy_field"        # rule 308.1 / 701.27
    EXECUTE_TAMASEED    = "execute_tamaseed"    # rule 204.2

    # ── Mana charge step (rule 503) ───────────────────────────────────────────
    CHARGE_MANA         = "charge_mana"         # place 1 card from hand to mana zone

    # ── Attack phase — attacker declaration (rule 506) ────────────────────────
    ATTACK_PLAYER       = "attack_player"       # attack opponent directly
    ATTACK_CREATURE     = "attack_creature"     # attack a tapped opponent creature

    # ── Attack phase — blocker declaration (rule 507) ─────────────────────────
    DECLARE_BLOCKER     = "declare_blocker"     # rule 701.12 — intercept attack
    DECLARE_GUARDMAN    = "declare_guardman"    # Guardman ability

    # ── Free execution responses (rule 112.3) ─────────────────────────────────
    USE_SHIELD_TRIGGER  = "use_shield_trigger"  # rule 112.3a — cast broken shield card for free
    USE_S_BACK          = "use_s_back"          # rule 112.3b — discard card to execute
    USE_NINJA_STRIKE    = "use_ninja_strike"    # rule 112.3c — summon during attack window
    USE_G_ZERO          = "use_g_zero"          # rule 112.3e — free summon condition met
    USE_ATTACK_CHANCE   = "use_attack_chance"   # rule 112.3f — free spell on attack
    USE_G_STRIKE        = "use_g_strike"        # rule 101.4b — same timing as S-Trigger
    HYPERIZE            = "hyperize"            # rule 816 — release Hyper Mode

    # ── Effect resolution choices ─────────────────────────────────────────────
    SELECT_TARGET       = "select_target"       # choose target(s) for an effect
    SELECT_MANA         = "select_mana"         # choose which mana cards to tap + civ used
    SELECT_CARD         = "select_card"         # choose a card (search, discard, etc.)
    SELECT_YES_NO       = "select_yes_no"       # optional effect: use or skip
    SELECT_ATTACK_ORDER = "select_attack_order" # choose order of multiple shield breaks
    SELECT_EVOLUTION_BASE = "select_evolution_base"  # choose which creature to evolve onto

    # ── Pass / end step ───────────────────────────────────────────────────────
    PASS                = "pass"                # end current step / skip optional action

    def __str__(self) -> str:
        return self.value


# ── Effect Types ──────────────────────────────────────────────────────────────

class EffectType(Enum):
    """Maps directly to card_effects.effect_type in DB."""
    TRIGGERED   = "triggered"    # rule 110.3b — fires when trigger_event occurs
    ACTIVATED   = "activated"    # rule 110.3c — player activates by paying cost
    STATIC      = "static"       # rule 110.3d — always active in zone
    KEYWORD     = "keyword"      # encoded keyword behavior
    REPLACEMENT = "replacement"  # rule 609 — "instead of X, Y happens"
    COST_MOD    = "cost_mod"     # modifies summoning/casting cost
    SPELL       = "spell"        # rule 110.3a — spell ability (on cast)


class TriggerEvent(Enum):
    """Maps directly to card_effects.trigger_event in DB."""
    ON_ENTER_BATTLE_ZONE  = "on_enter_battle_zone"
    ON_ATTACK             = "on_attack"
    ON_BREAK_SHIELD       = "on_break_shield"
    ON_DESTROY            = "on_destroy"
    ON_LEAVE_BATTLE_ZONE  = "on_leave_battle_zone"
    START_OF_TURN         = "start_of_turn"
    END_OF_TURN           = "end_of_turn"
    ON_SUMMON             = "on_summon"
    ON_CAST               = "on_cast"
    ON_SHIELD_TRIGGER     = "on_shield_trigger"
    ON_DRAW               = "on_draw"
    ON_MANA_CHARGE        = "on_mana_charge"
    ON_BLOCK              = "on_block"
    ON_BATTLE             = "on_battle"
    ON_WIN_BATTLE         = "on_win_battle"
    ON_DIRECT_ATTACK      = "on_direct_attack"   # rule 509 — attacking with 0 shields
    BEFORE_BREAK          = "before_break"        # rule 509.3 — before each shield break
    NONE                  = "none"


class EffectAction(Enum):
    """Maps directly to card_effects.effect_action in DB."""
    DRAW                = "draw"
    DESTROY             = "destroy"
    RETURN_TO_HAND      = "return_to_hand"
    SEARCH_DECK         = "search_deck"
    PUT_TO_MANA         = "put_to_mana"
    SUMMON_FREE         = "summon_free"
    PUT_TO_BATTLE_ZONE  = "put_to_battle_zone"
    PUT_TO_SHIELD       = "put_to_shield"
    ADD_TO_HAND         = "add_to_hand"
    DISCARD             = "discard"
    TAP                 = "tap"
    UNTAP               = "untap"
    POWER_MODIFY        = "power_modify"
    POWER_FIX           = "power_fix"           # rule 206.3 — fix to specific value
    CANNOT_ATTACK       = "cannot_attack"
    CANNOT_BE_BLOCKED   = "cannot_be_blocked"
    CANNOT_BE_DESTROYED = "cannot_be_destroyed"
    WIN_BATTLE          = "win_battle"
    BREAK_SHIELD        = "break_shield"
    LOOK_AT_TOP         = "look_at_top"
    SHUFFLE             = "shuffle"
    COST_REDUCE         = "cost_reduce"
    COST_INCREASE       = "cost_increase"
    GIVE_KEYWORD        = "give_keyword"
    BANISH_TO_ABYSS     = "banish_to_abyss"     # rule 410 / 701.33
    MOVE_ZONE           = "move_zone"
    REVEAL              = "reveal"              # rule 701.9
    GR_SUMMON           = "gr_summon"           # rule 701.30
    COPY_EFFECT         = "copy_effect"
    ATTACH_SEAL         = "attach_seal"         # rule 701.24
    REMOVE_SEAL         = "remove_seal"         # rule 701.23
    GACHINKO_JUDGE      = "gachinko_judge"      # rule 701.21
    HYPERIZE            = "hyperize"            # rule 816
    NONE                = "none"


# ── Game Result ───────────────────────────────────────────────────────────────

class GameResult(Enum):
    IN_PROGRESS   = "in_progress"
    PLAYER_0_WINS = "player_0_wins"
    PLAYER_1_WINS = "player_1_wins"
    DRAW          = "draw"    # internal no-winner result, not a normal DM outcome


# ── Global Effect Types ───────────────────────────────────────────────────────
# Effects that apply game-wide — not tied to a specific card instance.
# Examples: "players can't cast spells", "all creatures get +2000 power".

class GlobalEffectType(Enum):
    # ── Restrictions ──────────────────────────────────────────────────────────
    RESTRICT_SPELL_CIVILIZATION  = "restrict_spell_civilization"   # only certain civs allowed
    RESTRICT_SUMMON_CIVILIZATION = "restrict_summon_civilization"  # only certain civs allowed
    LOCK_ALL_SPELLS              = "lock_all_spells"               # no spells at all
    LOCK_ALL_CREATURES           = "lock_all_creatures"            # no summoning
    LOCK_CARD_TYPE               = "lock_card_type"               # no specific card type
    CANNOT_ATTACK                = "cannot_attack_global"
    CANNOT_CHARGE_MANA           = "cannot_charge_mana"

    # ── Power modifications (global) ──────────────────────────────────────────
    ALL_CREATURES_POWER_MOD      = "all_creatures_power_mod"       # +/- power to all
    ALL_CREATURES_POWER_FIX      = "all_creatures_power_fix"       # rule 206.3 fix

    # ── Keyword grants ────────────────────────────────────────────────────────
    GRANT_KEYWORD_ALL            = "grant_keyword_all"             # all creatures of type X gain Y

    # ── Other ─────────────────────────────────────────────────────────────────
    EXTRA_SHIELD_BREAK           = "extra_shield_break"


# ── Mana Selection entry ──────────────────────────────────────────────────────
# Used in Action.mana_selection to represent one mana card being tapped
# and which civilization the player is using it for (rule 112.2a).
# Multi-colored cards provide only ONE civilization per tap — player's choice.

class ManaUsage:
    """
    Represents one mana card being tapped to pay a cost.
    mana_uid: the uid of the ManaCard being tapped
    used_for_civ: which civilization this card is being used for
                  (None = just paying mana count, not a civ requirement)
    """
    __slots__ = ("mana_uid", "used_for_civ")

    def __init__(self, mana_uid: str, used_for_civ: "Civilization | None" = None):
        self.mana_uid     = mana_uid
        self.used_for_civ = used_for_civ

    def __repr__(self) -> str:
        civ = self.used_for_civ.value if self.used_for_civ else "any"
        return f"<ManaUsage uid={self.mana_uid} civ={civ}>"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ManaUsage):
            return self.mana_uid == other.mana_uid and self.used_for_civ == other.used_for_civ
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.mana_uid, self.used_for_civ))


# ── Constants ─────────────────────────────────────────────────────────────────
# From rules 100.x, 113.1

MAX_HAND_SIZE             = 10    # practical encoding cap (no hard rule limit)
MAX_MANA                  = 20    # practical cap
MAX_BATTLE_ZONE           = 8     # practical cap per player
MAX_SHIELDS               = 5     # rule 113.1
MAX_HYPERSPATIAL          = 8     # rule 100.3
MAX_ULTRA_GR              = 12    # rule 100.4
STARTING_HAND_SIZE        = 5     # rule 103.3
STARTING_SHIELD_COUNT     = 5     # rule 103.3
MAX_TURNS                 = 30    # legacy cap; training should prefer max_steps
MAX_DECK_SIZE             = 40    # rule 100.2
MIN_DECK_SIZE             = 40    # rule 100.2
MAX_COPIES_PER_CARD       = 4     # rule 100.2a
