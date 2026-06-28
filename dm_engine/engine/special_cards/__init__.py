"""engine/special_cards — Card-family-specific mechanics (Rule 800-822)."""
from .psychic import awaken_psychic_creature, link_psychic_cells, should_apply_psychic_release, apply_psychic_release
from .psychic_super import link_release_psychic_super
from .dragheart import dragsolve_dragheart, should_apply_dragon_evasion, apply_dragon_evasion, dragon_soul_evasion
from .twinpact import flip_twinpact
from .forbidden_heartbeat import flip_forbidden
from .hyper_mode import swap_hyper_mode
from .king_cells import combine_king_cells
from .star_evolution import should_apply_star_evo_replacement
from .neo import should_apply_gneo_all_leave_replacement
from .zerom import move_zerom_to_battle
from .gr_creature import move_ultra_gr_to_battle
