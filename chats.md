# user

> My goal for this project is to make an ai (with neral network) who can play duel masters also can build compititive decks. 1st make a bot create a simulation with 2 copy of bot as players and they will play create data points for each player side and overall use those data points to teach the ai. Don't generate code just give me guidance

---

# assistant
This is a well-scoped AlphaZero-style project. Let me give you a proper architectural breakdown.

---

## The Full Pipeline

```
Game Engine → Self-Play Bot → Datapoints → Neural Net → Stronger Bot → repeat
```

This is exactly how AlphaGo Zero / AlphaZero worked. You don't need human game data — the system bootstraps itself from random play and improves iteratively.

---

## Phase 1 — Game Engine (Foundation)

This is the hardest and most important part. Everything else depends on it being correct.

### What it needs to represent

The **game state** must be fully serializable at any moment:

- Both players: hand, mana zone (tapped/untapped, civilizations), battle zone (each creature: power, tapped/untapped, summoning sickness, keywords), shield zone (count, face-down contents), graveyard, deck size
- Current phase, current turn number, whose turn it is
- Active effects (temporary power boosts, "until end of turn" effects, continuous static effects)
- The stack — Duel Masters doesn't have an explicit stack like MTG but triggered effects, Shield Triggers, and Ninja Strike timing need ordered resolution

### The trickiest rules to implement correctly

**Priority order matters a lot:**
1. State-based actions check first (creature at 0 power → destroy, deck at 0 → lose)
2. Triggered effects queue up and resolve in a defined order
3. Shield Triggers happen immediately when a shield is broken, interrupting the attack sequence
4. Ninja Strike has specific timing windows (before/after blocker declared)
5. Blocker / Guardman must be offered before the attack resolves

**Evolution creatures** — need to track the card underneath. The base card's race matters for evolution targets.

**Multi-civilization cards** — mana tapping must track which civilizations are available, not just count.

**The "cannot attack players" rule** — creatures with this can still attack other creatures.

Don't try to implement every card interaction upfront. Start with a **vanilla creature + spell** ruleset and add keyword complexity incrementally.

### Recommended engine architecture

```
GameState (dataclass, fully immutable snapshot)
    ↓
ActionGenerator → list of LegalAction objects
    ↓
ActionExecutor → applies action → returns new GameState
    ↓
StateBasedActionChecker → runs after every action, returns modified GameState
    ↓
TriggeredEffectResolver → queues and resolves triggers
```

**Immutability is critical** — MCTS requires branching from any state without side effects. Each action returns a new GameState copy, never mutates in place.

---

## Phase 2 — State Encoder

Converts a GameState into a fixed-size float vector for the neural net.

### Design principles

**The neural net can only see numbers.** Every game concept must become a number or a set of numbers.

**Card representation** — you have two choices:

- **Card ID as index** — one-hot or embedding lookup. Simple but the net has to learn what each card does from scratch through play.
- **Card feature vector** — cost, power, civilization (one-hot), card type (one-hot), keyword flags (blocker=1, double breaker=1, etc.), effect type flags. The net starts with structural knowledge about cards.

The second approach learns much faster. A 40–60 dimensional feature vector per card is reasonable.

**Zone encoding** — for each zone (hand, mana, battle zone, shields) you need a fixed-size representation. Since zones have variable numbers of cards, you have options:

- Fixed maximum size with padding (e.g. always encode 40 hand slots, zero-pad empty ones) — simple, works well
- Aggregated zone summary (total cards, sum of powers, civilization flags present) — loses individual card info but is compact

For the battle zone, encode each creature slot individually since power and tap state matter per creature.

**What the state vector should contain (per player, both players):**
- Hand: feature vectors for each card (or padded fixed slots)
- Mana zone: count, tapped count, civilization availability flags (5 bits)
- Battle zone: per creature — feature vector + tapped flag + summoning sickness flag
- Shield count
- Graveyard: summary only (count, notable cards present as flags)
- Deck size remaining

**Plus game-level info:**
- Current phase (one-hot over 10 phases)
- Turn number (normalized)
- Whose turn (binary flag)

Typical total vector size: **400–800 floats** depending on how detailed you encode cards. Start smaller and expand if the net isn't learning.

---

## Phase 3 — MCTS (Monte Carlo Tree Search)

This is the "thinking" mechanism for the bot. For each decision point, MCTS explores the game tree to find the best action.

### How it works

```
From current state S:
  Repeat N times (your thinking budget):
    1. SELECT    — walk the tree using UCB1 formula, balancing exploration vs exploitation
    2. EXPAND    — at a leaf node, generate legal actions and add children
    3. EVALUATE  — run the neural net on this state → get (policy, value)
    4. BACKPROP  — update visit counts and values up the tree
  
  Return: action with highest visit count (most explored = best)
```

**UCB1 formula** (what drives selection):

```
score = Q(s,a) + c_puct × P(s,a) × √(N(s)) / (1 + N(s,a))
```

Where Q is average value seen from this action, P is the neural net's prior probability for this action, N(s) is parent visits, N(s,a) is this action's visits. `c_puct` controls exploration — typically 1.0–2.0.

### Key decisions for your implementation

**Rollout policy** — AlphaZero uses NO random rollouts. The neural net's value head directly estimates win probability from any state. This is better than random rollouts for complex games.

**Thinking budget** — during self-play, use 200–800 simulations per move. This is your main quality vs speed tradeoff. More simulations = better play = better training data = slower generation.

**Action space** — define a fixed action index space upfront:
- Summon creature (one index per unique card in the deck)
- Cast spell (one index per unique spell)
- Charge mana (one index per unique card)
- Attack with creature X against player / creature Y
- Declare blocker / don't block
- Pass phase

The neural net's policy head outputs a probability over this entire action space. You mask illegal actions to zero before sampling.

**Dirichlet noise** — add small random noise to the root node's policy during self-play to ensure exploration of unusual lines. Without this, games converge to the same opening every time.

---

## Phase 4 — Neural Network Architecture

### Architecture

```
Input: state vector (flat float array)
    ↓
Shared Trunk: several fully-connected layers with residual connections
    ↓                    ↓
Policy Head          Value Head
(action probs)       (win probability)
    ↓                    ↓
softmax over         tanh → [-1, +1]
legal actions
```

**Why residual connections** — they let gradients flow through deep networks without vanishing. Use blocks of: Linear → BatchNorm → ReLU → Linear → BatchNorm → add residual → ReLU.

**Trunk size** — start with 3–4 residual blocks, 256 hidden units. Scale up if it's underfitting.

**Policy head** — outputs logits over your full action space (say 200 actions). You apply a mask to zero out illegal actions then softmax.

**Value head** — outputs a single scalar through tanh. +1 means current player wins, -1 means current player loses.

**Loss function** — two components added together:
- Policy loss: cross-entropy between MCTS visit distribution π and network's policy p
- Value loss: MSE between network's value estimate v and actual game outcome z (+1/-1)
- Total loss = (z - v)² - π · log(p) + L2 regularization

---

## Phase 5 — Self-Play Data Generation

### The datapoint structure

For every decision made during a game, you record:

```
{
  state_vector:   float[],     # encoded game state at decision point
  mcts_policy:    float[],     # visit count distribution from MCTS (normalized)
  game_outcome:   float,       # +1 if this player won, -1 if lost
  turn_number:    int,
  phase:          str,
  player:         int          # 0 or 1
}
```

The `mcts_policy` is the key — it's better than just the action taken because it captures how strongly MCTS preferred each option. A move with 90% visits is a much stronger signal than one with 51%.

### Self-play game structure

```
Initialize two instances of the SAME bot (same neural net weights)
    ↓
Play a complete game, both sides using MCTS
    ↓
At each decision point → record datapoint
    ↓
Game ends → label all datapoints with game outcome
    ↓
Add all datapoints from both players to the replay buffer
```

**Both players generate datapoints** — from the perspective of whoever is making the decision. The game outcome is +1 if that player won, -1 if they lost.

### Replay buffer

Don't train on just the most recent game. Maintain a buffer of the last 500,000–1,000,000 datapoints. Sample mini-batches randomly for training. This breaks temporal correlations and stabilizes training.

---

## Phase 6 — Training Loop

```
Generation 0:  Random weights → self-play 100 games → 10,000 datapoints
    ↓
Train net on those datapoints for N steps
    ↓
Generation 1:  Updated weights → self-play 100 games → better datapoints
    ↓
Train again
    ↓
repeat...
```

### Evaluation between generations

After each training cycle, pit the new net against the previous best. If the new net wins 55%+ of 40 evaluation games, it becomes the new best and generates the next batch of self-play data. If not, keep training the current version.

This gating prevents catastrophic forgetting — you never replace a good net with a worse one.

### Practical training notes

- **Learning rate schedule**: start at 0.001, decay to 0.0001 over training
- **Batch size**: 512–2048 datapoints per step
- **Training steps per generation**: 1000–5000 steps before next self-play cycle
- **GPU**: you'll want one for training. Self-play (MCTS + net inference) can run on CPU but is faster with GPU

---

## Phase 7 — Deck Building Agent

This is a separate problem from playing. Two approaches:

### Approach A — Evolutionary/search based (simpler)

Start with a random legal 40-card deck. Mutate it (swap cards in/out), evaluate it by playing N self-play games against a fixed opponent, keep mutations that improve win rate. Repeat. This is essentially a genetic algorithm.

The evaluator here IS the trained game-playing neural net — you use it to quickly simulate outcomes without full MCTS.

### Approach B — Neural deck builder (harder, more interesting)

Train a separate network that takes a partial deck as input and outputs which card to add next. Reward signal: win rate of the completed deck.

This is a sequential decision problem — you're building the deck one card at a time. It can be framed as another RL problem where the "game" is deck construction and the reward is tournament win rate.

---

## Recommended Build Order

```
1. Game engine (pure rules, no AI) — get this right first
   └── Unit test every rule: SBAs, triggers, phase transitions
   
2. State encoder — fixed-size vector from GameState

3. Random bot — plays legal random moves, used to verify engine correctness
   └── Run 1000 games, check for illegal states, infinite loops, wrong outcomes

4. Neural net (untrained) — random weights, verify forward pass works

5. MCTS wired to random net — verify tree search produces legal action distributions

6. Self-play loop — generate first 10,000 datapoints with random net

7. First training run — train on those datapoints, verify loss decreases

8. Evaluation loop — random weights vs trained weights, verify trained wins more

9. Iterative improvement — run the full loop, watch Elo climb

10. Deck builder — add after the player is competent (Gen 5-10+)
```

---

## Biggest Risk Areas

**Game engine correctness** is everything. A bug in rule resolution means your AI learns from corrupted data and develops exploits of the bug rather than real strategy. Write exhaustive unit tests for edge cases — especially Shield Triggers during multi-break attacks, Ninja Strike timing, and evolution resolution.

**Action space explosion** — Duel Masters has many cards and many possible targets. If your action space is too large (say 10,000+ actions), the policy head becomes hard to train. Group actions by type and use a hierarchical policy: first choose action type (summon/attack/spell/charge), then choose the specific card/target.

**Game length** — if games rarely terminate, MCTS sees no reward signal. Add a turn limit (say 30 turns) with a draw outcome. This forces the net to learn to win efficiently.

**Card diversity** — start with a small fixed card pool (50–100 cards) and two fixed test decks. Don't try to support all 8000+ cards from day one. Get the learning loop working first, then expand.

---

# user
> How parse effect will work and how it will help bot to play?

# assistant

Yes — you already built `dm_ingest` which parsed the rules PDF into PostgreSQL + ChromaDB. Let me show you exactly where each table and the vector store plug into the bot.

---

## What You Already Have

From `dm_ingest`, recall the schema:

```
dm_rules              — 725 rules with engine tags
dm_keywords           — 35 keywords with behavior flags  
dm_game_phases        — 10 phases (turn_start → turn_end)
dm_phase_actions      — ordered actions per phase
dm_state_based_actions — 13 SBAs with condition_json + effect_json
dm_rule_relations     — parent → child + exception links

ChromaDB: dm_rules collection — all 725 rules semantically embedded
```

Each piece has a specific job in the bot. They are **not** used the same way.

---

## PostgreSQL Tables — Hard-Coded Engine Logic

These drive the deterministic parts of the engine. No fuzzy search, no LLM. Pure structured queries.

### `dm_game_phases` + `dm_phase_actions` → Turn Structure

This tells the engine **what is legal at every moment** of a turn.

```
turn_start
    ↓
untap_step    → untap all your tapped cards
    ↓
draw_step     → draw 1 card (turn 2+)
    ↓
charge_step   → optionally put 1 card face-down into mana
    ↓
main_step     → summon creatures / cast spells
    ↓
attack_step   → declare attackers
    ↓
    ├── block_step   → opponent may declare blockers
    ├── battle_step  → power comparison if blocked
    └── shield_break → break shields if unblocked
    ↓
turn_end      → end of turn triggers, effect expiry
```

At each phase transition, the engine queries `dm_phase_actions` to know what actions to offer the current player. The MCTS bot cannot attempt an action outside the current phase — this is enforced mechanically.

```python
def get_legal_actions(game_state):
    phase = game_state.current_phase
    
    # What is structurally allowed this phase?
    phase_actions = db.query("""
        SELECT action_type, who, is_mandatory
        FROM dm_phase_actions
        WHERE phase_id = %s
        ORDER BY sequence
    """, (phase.id,))
    
    # Cross-reference with card_effects for card-specific actions
    # (e.g. speed attacker can attack turn 1 — overrides normal rules)
    return build_legal_action_list(phase_actions, game_state)
```

The bot never needs to "know" that you can only attack during the attack step — it's in the DB. The engine enforces it structurally.

---

### `dm_state_based_actions` → Automatic Game Rules

These are the 13 SBAs (703.4a–703.4m) stored with `condition_json` and `effect_json`. They run **automatically after every single action**, before any player gets priority.

```python
def check_state_based_actions(game_state):
    sbas = db.query("""
        SELECT condition_json, effect_json, priority
        FROM dm_state_based_actions
        ORDER BY priority ASC  -- lower number = checked first
    """)
    
    changed = True
    while changed:  # keep checking until no SBA fires
        changed = False
        for sba in sbas:
            if evaluate_condition(sba.condition_json, game_state):
                game_state = apply_effect(sba.effect_json, game_state)
                changed = True
    
    return game_state
```

**What the 13 SBAs cover:**

| SBA | condition_json checks | effect_json does |
|---|---|---|
| 703.4a | creature.power <= 0 | destroy it |
| 703.4b | player.deck_size == 0 | that player loses |
| 703.4c | player.shields == 0 AND took direct attack | that player loses |
| 703.4d | evolution with no base | remove from play |
| ... | ... | ... |

The bot never has to "know" these rules explicitly. After every action it just calls `check_state_based_actions()` and the DB handles it. **This is what makes the engine correct by construction** — SBAs can't be forgotten or skipped.

---

### `dm_keywords` → Keyword Behavior Flags

Your 35 keyword rows have behavior flags like:

```sql
keyword        | is_evasion | is_triggered | requires_target | phase_relevant
───────────────┼────────────┼──────────────┼─────────────────┼───────────────
blocker        | false      | false        | false           | block_step
double_breaker | false      | false        | false           | shield_break
shield_trigger | false      | true         | false           | any
speed_attacker | true       | false        | false           | attack_step
ninja_strike   | false      | true         | true            | block_step
```

These flags do two things:

**1. Engine enforcement** — when a creature with `speed_attacker=true` is in the battle zone, the engine removes the "summoning sickness" restriction for it. When `blocker=true`, the engine offers the block action to that creature's controller during block_step.

**2. State vector features** — each keyword flag becomes a bit in the card's feature vector for the neural net:

```python
def encode_card_features(card_id):
    keywords = db.query("""
        SELECT dk.keyword, dk.is_evasion, dk.is_triggered, 
               dk.requires_target, dk.phase_relevant
        FROM card_keywords ck
        JOIN dm_keywords dk ON dk.keyword = ck.keyword
        WHERE ck.card_id = %s
    """, (card_id,))
    
    return {
        "has_blocker":        any(k.keyword == "blocker" for k in keywords),
        "has_double_breaker": any(k.keyword == "double_breaker" for k in keywords),
        "has_speed_attacker": any(k.keyword == "speed_attacker" for k in keywords),
        "has_shield_trigger": any(k.keyword == "shield_trigger" for k in keywords),
        "has_ninja_strike":   any(k.keyword == "ninja_strike" for k in keywords),
        # ... all 35 keywords as binary flags
    }
```

The neural net receives these flags and learns — through self-play — that `has_double_breaker=1` cards are worth attacking with aggressively, that `has_blocker=1` cards are worth keeping untapped, etc.

---

### `dm_rule_relations` → Exception Handling

This table stores parent→child and exception links between rules. The engine uses it when a card effect seems to contradict a base rule:

```
rule 508.1: "A creature cannot attack the turn it is put into the battle zone"
    └── exception → rule 508.1a: "unless it has Speed Attacker"
```

When resolving whether a creature can attack, the engine doesn't just check the base rule — it walks the exception chain:

```python
def can_attack_this_turn(creature, game_state):
    # Base rule: summoning sickness
    if creature.entered_this_turn:
        # Check exception chain for this rule
        exceptions = db.query("""
            SELECT child_rule_number FROM dm_rule_relations
            WHERE parent_rule_number = '508.1'
            AND relation_type = 'exception'
        """)
        # Does this creature qualify for any exception?
        for exc in exceptions:
            if qualifies_for_exception(exc, creature, game_state):
                return True
        return False
    return True
```

This is important for cards that grant keywords or override base rules temporarily.

---

## ChromaDB — The RAG Fallback

This is used **only** for edge cases the structured tables can't resolve. Think of it as the rules lawyer you consult when an unusual situation comes up.

### When does the engine hit ChromaDB?

Three situations:

**1. Ambiguous effect interaction**
Two card effects interact in a way that isn't covered by `card_effects` parsing. Example: does "cannot be destroyed" protect against being sent to mana zone?

**2. Novel card text the parser tagged as low confidence**
Any `card_effects` row with `parse_confidence < 0.7` that gets triggered in a real game.

**3. Explicit rule lookup for complex timing**
Ninja Strike, Revolution Change, and other conditional timing abilities require knowing the exact rule text to resolve correctly.

### How it works at runtime

```python
def resolve_edge_case(event_description, game_state):
    
    # 1. Build the query from the game situation
    query = f"""
        {event_description}
        Current phase: {game_state.phase}
        Cards involved: {game_state.active_cards}
    """
    
    # 2. Search ChromaDB — your DMRulesRetriever class
    retriever = DMRulesRetriever("./dm_chroma_db")
    relevant_rules = retriever.search(
        query,
        phase=game_state.phase,
        n=5
    )
    
    # 3. Send to Claude with the rules context
    context = retriever.build_context_for_event(
        event_description,
        current_phase=game_state.phase
    )
    
    resolution = claude.resolve(context, game_state)
    
    # 4. Cache this resolution so it never hits Claude again
    edge_case_cache[hash(event_description)] = resolution
    
    return resolution
```

The caching step is critical — the first time a Ninja Strike timing question comes up, Claude resolves it. Every subsequent time, it's instant from cache. Over thousands of self-play games, the cache fills up and Claude is called less and less.

---

## The Complete Runtime Query Flow

Here is exactly what gets queried at each moment of a game, in order:

```
Player puts a card into play
        │
        ├─► dm_phase_actions
        │   "Is summoning a creature legal in the current phase?"
        │   → yes, proceed
        │
        ├─► card_effects (trigger_event = "on_enter_battle_zone")  
        │   "Does this card have an ETB effect?"
        │   → yes: draw 2 cards → execute
        │
        ├─► dm_state_based_actions (run after EVERY action)
        │   Check all 13 SBAs in priority order
        │   → no SBA fires → continue
        │
        ├─► card_effects (effect_type = "static")
        │   "Are there any static effects that continuously apply?"
        │   → yes: creature with "all your fire creatures get +1000 power"
        │   → update power values in state
        │
        └─► dm_keywords (for the new creature)
            "Does it have speed attacker? Blocker? Double breaker?"
            → update legal actions for next step
```

```
Player declares an attack
        │
        ├─► dm_phase_actions
        │   "Is attacking legal now?"
        │
        ├─► dm_keywords → check summoning sickness
        │   + dm_rule_relations → check speed attacker exception
        │
        ├─► card_effects (trigger_event = "on_attack")
        │   "Does attacker have an attack trigger?"
        │   → execute if yes
        │
        ├─► Opponent offered block
        │   └─► dm_keywords → which creatures have blocker/guardman?
        │
        ├─► Battle resolution
        │   └─► dm_keywords → double/triple/world breaker for shield breaks
        │                   → slayer for destruction rules
        │
        ├─► Shield trigger check
        │   └─► card_effects (trigger_event = "on_shield_trigger")
        │       ChromaDB if timing is ambiguous
        │
        └─► dm_state_based_actions (after each shield break)
            "Did any creature reach 0 power? Did a player lose?"
```

---

## What the Bot Actually "Knows" vs What It Learns

This is the important distinction:

**Hard-coded from your DB (the bot knows this from birth):**
- Turn structure and phase legality (`dm_game_phases`, `dm_phase_actions`)
- Automatic game rules (`dm_state_based_actions`)
- Keyword mechanical behavior (`dm_keywords`)
- Card effect execution (`card_effects`)
- Rule exceptions and interactions (`dm_rule_relations`)

**Learned through self-play (the bot figures this out on its own):**
- Which cards are worth playing in which situations
- When to attack vs hold back
- How to sequence plays for maximum effect
- Which card combinations are synergistic
- How to manage mana curve
- When to charge a card to mana vs keep it in hand
- Which shields are worth breaking first
- How aggressive vs defensive to play in a given board state

The DB gives the bot **correct mechanics**. Self-play gives the bot **strategy**. Neither can substitute for the other — correct mechanics without strategy means a bot that plays legal moves randomly; strategy without correct mechanics means a bot that learns from corrupted game states and develops invalid patterns.

Your `dm_ingest` work is already the foundation. The 725 rules, 13 SBAs, 10 phases, and 35 keywords are the skeleton of the game engine. Everything the bot learns through self-play sits on top of that skeleton.

---

# user

> How to build 0 genaration bot? What Library to use? Will it have neural network?

# assistant

Great question — Generation 0 is the starting point. Let me be precise about what it is and isn't.

---

## What Generation 0 Actually Is

Generation 0 is **not** a trained bot. It is a **random legal move player** wired to a real game engine with a real neural network that has **random untrained weights**.

The purpose is not to play well. The purpose is to:
- Verify the game engine is correct
- Generate the first batch of training datapoints
- Give the neural net its first signal to learn from

```
Gen 0 = Random weights neural net + MCTS + correct game engine
              ↓
         plays games against itself
              ↓
         generates datapoints (bad quality but structurally correct)
              ↓
         first training run
              ↓
         Gen 1 (slightly less random)
```

---

## Libraries

Here is exactly what to use and why for each component.

### Game Engine

**No library — pure Python.**

Do not use any existing card game framework. Your game is too specific. Pure Python dataclasses + functions. The engine needs to be:
- Fully deterministic
- Copyable (for MCTS branching)
- Fast enough to run thousands of simulations

```
Python dataclasses  — GameState, Card, Zone, Effect
psycopg2            — query dm_rules, card_effects, dm_state_based_actions
```

### Neural Network

**PyTorch.**

Not TensorFlow, not Keras. PyTorch because:
- Dynamic computation graph — easy to debug during development
- `torch.jit.script` for later optimization when you need speed
- Best ecosystem for RL research (most AlphaZero implementations use it)
- You control exactly what happens — no magic layers hiding behavior

```
torch        — neural net, autograd, training
torch.nn     — Linear, BatchNorm, ReLU, residual blocks
torch.optim  — Adam optimizer
```

### MCTS

**No library — pure Python.**

Existing MCTS libraries (like `mcts` on PyPI) are built for simple board games with small action spaces. Duel Masters has variable action spaces, hidden information (opponent's hand), and complex state transitions. You need to write it yourself — it is not as complex as it sounds. The core MCTS loop is about 100 lines of clean Python.

### Self-Play Infrastructure

```
multiprocessing   — run multiple self-play games in parallel (built-in Python)
numpy             — state vector construction, data manipulation
collections.deque — replay buffer (fixed-size, fast append/pop)
pickle or h5py    — save/load datapoints to disk
```

### Database

```
psycopg2          — PostgreSQL queries (you already use this)
chromadb          — RAG fallback (you already have this)
```

### Supporting

```
tqdm              — progress bars during self-play and training
tensorboard       — visualize training loss, value accuracy, policy entropy
loguru            — structured logging (better than Python logging)
```

---

## Full Generation 0 Architecture

### Step 1 — GameState Dataclass

```python
@dataclass
class GameState:
    # Both players
    hands:        tuple[list[Card], list[Card]]
    mana_zones:   tuple[list[Card], list[Card]]    # (card, tapped)
    battle_zones: tuple[list[Creature], list[Creature]]
    shield_zones: tuple[list[Card], list[Card]]    # face-down
    graveyards:   tuple[list[Card], list[Card]]
    deck_sizes:   tuple[int, int]
    
    # Turn info
    current_player: int          # 0 or 1
    current_phase:  Phase
    turn_number:    int
    
    # Effect tracking
    active_effects: list[ActiveEffect]  # "until end of turn" effects
    
    def copy(self) -> "GameState":
        return deepcopy(self)
    
    def is_terminal(self) -> bool:
        return (self.deck_sizes[0] == 0 or 
                self.deck_sizes[1] == 0 or
                # shields == 0 AND took direct attack is handled by SBA
                self.turn_number > 30)  # draw cap
    
    def winner(self) -> Optional[int]:
        # returns 0, 1, or None for draw
```

**Key rule**: `GameState` must be fully copyable with no shared references. MCTS makes thousands of copies to explore branches. Use `copy.deepcopy()` initially — optimize later if it becomes a bottleneck.

---

### Step 2 — Action Representation

Define every possible action as a typed object:

```python
@dataclass(frozen=True)
class Action:
    action_type: ActionType   # enum
    card_id:     Optional[int]
    target_id:   Optional[int]
    target_zone: Optional[str]

class ActionType(Enum):
    SUMMON_CREATURE   = 0
    CAST_SPELL        = 1
    CHARGE_MANA       = 2
    ATTACK_PLAYER     = 3
    ATTACK_CREATURE   = 4
    DECLARE_BLOCKER   = 5
    PASS_PRIORITY     = 6
    CHOOSE_TARGET     = 7   # for effects that require selection
```

Actions must be **hashable** (hence `frozen=True`) because MCTS stores them as dict keys in the tree nodes.

---

### Step 3 — Action Generator

This queries your DB to build the legal action list:

```python
def get_legal_actions(state: GameState, db_conn) -> list[Action]:
    player = state.current_player
    phase  = state.current_phase
    legal  = []

    if phase == Phase.CHARGE:
        # Can charge any 1 card from hand to mana
        for card in state.hands[player]:
            legal.append(Action(ActionType.CHARGE_MANA, card.id, None, None))
        legal.append(Action(ActionType.PASS_PRIORITY, None, None, None))

    elif phase == Phase.MAIN:
        # Query what card_effects + dm_phase_actions allow
        for card in state.hands[player]:
            if can_summon(card, state, db_conn):
                legal.append(Action(ActionType.SUMMON_CREATURE, card.id, None, None))
            if can_cast(card, state, db_conn):
                legal.append(Action(ActionType.CAST_SPELL, card.id, None, None))
        legal.append(Action(ActionType.PASS_PRIORITY, None, None, None))

    elif phase == Phase.ATTACK:
        for creature in state.battle_zones[player]:
            if can_attack(creature, state, db_conn):
                # Can attack player
                legal.append(Action(ActionType.ATTACK_PLAYER, creature.id, None, None))
                # Can attack each opponent creature
                for target in state.battle_zones[1 - player]:
                    legal.append(Action(ActionType.ATTACK_CREATURE, 
                                        creature.id, target.id, None))
        legal.append(Action(ActionType.PASS_PRIORITY, None, None, None))

    return legal
```

---

### Step 4 — Neural Network

Here is the exact architecture for Generation 0:

```python
import torch
import torch.nn as nn

class DuelMastersNet(nn.Module):
    
    def __init__(self, state_dim: int, action_dim: int, hidden_dim: int = 256):
        super().__init__()
        
        # Shared trunk — 4 residual blocks
        self.input_layer = nn.Linear(state_dim, hidden_dim)
        self.trunk = nn.ModuleList([
            ResidualBlock(hidden_dim) for _ in range(4)
        ])
        
        # Policy head — what action to take
        self.policy_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, action_dim)
            # No softmax here — apply after masking illegal actions
        )
        
        # Value head — who is winning
        self.value_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, 1),
            nn.Tanh()   # output in [-1, +1]
        )
    
    def forward(self, state_vec: torch.Tensor, legal_mask: torch.Tensor):
        x = torch.relu(self.input_layer(state_vec))
        
        for block in self.trunk:
            x = block(x)
        
        # Policy — mask illegal actions then softmax
        policy_logits = self.policy_head(x)
        policy_logits[~legal_mask] = float("-inf")   # mask illegal
        policy = torch.softmax(policy_logits, dim=-1)
        
        # Value
        value = self.value_head(x)
        
        return policy, value


class ResidualBlock(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.block = nn.Sequential(
            nn.Linear(dim, dim),
            nn.BatchNorm1d(dim),
            nn.ReLU(),
            nn.Linear(dim, dim),
            nn.BatchNorm1d(dim),
        )
        self.relu = nn.ReLU()
    
    def forward(self, x):
        return self.relu(x + self.block(x))  # residual connection
```

**State dim** — the length of your encoded state vector. Start with ~512.  
**Action dim** — the total number of possible actions across your entire action space. For a 40-card deck pool, roughly 150–300.

At Generation 0, the weights are randomly initialized by PyTorch. The net will output near-uniform policies and near-zero values. That is correct and expected.

---

### Step 5 — State Encoder

Converts a `GameState` into a flat float tensor:

```python
def encode_state(state: GameState, perspective: int, db_conn) -> torch.Tensor:
    """
    Encode state from the perspective of `perspective` player (0 or 1).
    Always encode "my" info first, then "opponent" info.
    This way the net learns player-agnostic strategy.
    """
    me  = perspective
    opp = 1 - perspective
    
    features = []
    
    # ── My hand ───────────────────────────────────────────────────────
    # Fixed 10 slots (max hand size), zero-padded
    for i in range(10):
        if i < len(state.hands[me]):
            features.extend(encode_card(state.hands[me][i], db_conn))
        else:
            features.extend([0.0] * CARD_FEATURE_DIM)
    
    # ── My mana zone ──────────────────────────────────────────────────
    features.append(len(state.mana_zones[me]) / 20.0)          # total mana (normalized)
    features.append(sum(1 for c,t in state.mana_zones[me] if not t) / 20.0)  # untapped
    # Civilization availability (5 flags)
    civs = get_available_civilizations(state.mana_zones[me])
    features.extend([float(c in civs) for c in ["Fire","Water","Nature","Light","Darkness"]])
    
    # ── My battle zone ────────────────────────────────────────────────
    # Fixed 8 slots
    for i in range(8):
        if i < len(state.battle_zones[me]):
            creature = state.battle_zones[me][i]
            features.extend(encode_card(creature.card, db_conn))
            features.append(float(creature.tapped))
            features.append(float(creature.summoning_sick))
        else:
            features.extend([0.0] * (CARD_FEATURE_DIM + 2))
    
    # ── My shields ────────────────────────────────────────────────────
    features.append(len(state.shield_zones[me]) / 5.0)
    
    # ── Opponent info (same structure) ────────────────────────────────
    # Opponent hand: we can't see cards, just count
    features.append(len(state.hands[opp]) / 10.0)
    # Opponent mana, battle zone, shields — same encoding
    # ...
    
    # ── Game phase ────────────────────────────────────────────────────
    phase_onehot = [0.0] * 10
    phase_onehot[state.current_phase.value] = 1.0
    features.extend(phase_onehot)
    
    # ── Turn info ─────────────────────────────────────────────────────
    features.append(state.turn_number / 30.0)
    features.append(float(state.current_player == perspective))
    
    return torch.tensor(features, dtype=torch.float32)


def encode_card(card: Card, db_conn) -> list[float]:
    """Fixed CARD_FEATURE_DIM float vector for one card."""
    kw = get_keyword_flags(card.id, db_conn)  # from dm_keywords
    ef = get_effect_flags(card.id, db_conn)   # from card_effects
    
    return [
        card.cost / 15.0,                          # normalized cost
        (card.power or 0) / 15000.0,               # normalized power
        float("Fire"     in card.civilizations),
        float("Water"    in card.civilizations),
        float("Nature"   in card.civilizations),
        float("Light"    in card.civilizations),
        float("Darkness" in card.civilizations),
        float(card.card_type == "Creature"),
        float(card.card_type == "Spell"),
        float(card.card_subtype == "Evolution"),
        # Keywords from dm_keywords
        float(kw.get("blocker", False)),
        float(kw.get("double_breaker", False)),
        float(kw.get("triple_breaker", False)),
        float(kw.get("speed_attacker", False)),
        float(kw.get("shield_trigger", False)),
        float(kw.get("slayer", False)),
        # Effect flags from card_effects
        float(ef.get("has_etb_draw", False)),
        float(ef.get("has_etb_destroy", False)),
        float(ef.get("has_etb_search", False)),
        float(ef.get("has_attack_trigger", False)),
        float(ef.get("has_power_attacker", False)),
        float(ef.get("has_cost_reducer", False)),
        # ... more flags
    ]

CARD_FEATURE_DIM = 22  # length of encode_card output
```

---

### Step 6 — MCTS

The core of the bot's decision-making:

```python
class MCTSNode:
    def __init__(self, state: GameState, prior: float):
        self.state    = state
        self.prior    = prior       # P(s,a) from neural net
        self.visits   = 0           # N(s,a)
        self.value_sum = 0.0        # sum of backed-up values
        self.children: dict[Action, MCTSNode] = {}
        self.is_expanded = False
    
    @property
    def Q(self) -> float:
        if self.visits == 0:
            return 0.0
        return self.value_sum / self.visits
    
    def UCB(self, parent_visits: int, c_puct: float = 1.5) -> float:
        return self.Q + c_puct * self.prior * (parent_visits ** 0.5) / (1 + self.visits)


class MCTS:
    def __init__(self, net: DuelMastersNet, db_conn, n_simulations: int = 400):
        self.net   = net
        self.db    = db_conn
        self.n_sim = n_simulations
    
    def run(self, root_state: GameState) -> dict[Action, float]:
        root = MCTSNode(root_state, prior=1.0)
        
        # Add Dirichlet noise at root for exploration during self-play
        self._add_dirichlet_noise(root)
        
        for _ in range(self.n_sim):
            node = root
            path = [node]
            
            # ── SELECT ────────────────────────────────────────────────
            while node.is_expanded and not node.state.is_terminal():
                action, node = self._select_child(node)
                path.append(node)
            
            # ── EVALUATE ──────────────────────────────────────────────
            if node.state.is_terminal():
                value = self._terminal_value(node.state, root_state.current_player)
            else:
                value = self._expand_and_evaluate(node)
            
            # ── BACKPROPAGATE ─────────────────────────────────────────
            for n in path:
                n.visits += 1
                n.value_sum += value
                value = -value  # flip for opponent's perspective
        
        # Return visit count distribution (the training target π)
        total = sum(c.visits for c in root.children.values())
        return {
            action: child.visits / total
            for action, child in root.children.items()
        }
    
    def _select_child(self, node: MCTSNode) -> tuple[Action, MCTSNode]:
        parent_visits = node.visits
        return max(
            node.children.items(),
            key=lambda ac: ac[1].UCB(parent_visits)
        )
    
    def _expand_and_evaluate(self, node: MCTSNode) -> float:
        state = node.state
        legal = get_legal_actions(state, self.db)
        
        # Build legal mask for the full action space
        state_vec   = encode_state(state, state.current_player, self.db)
        legal_mask  = build_legal_mask(legal)
        
        with torch.no_grad():
            policy, value = self.net(
                state_vec.unsqueeze(0), 
                legal_mask.unsqueeze(0)
            )
        
        policy = policy.squeeze(0)
        value  = value.squeeze(0).item()
        
        # Create child nodes
        for action in legal:
            prior = policy[action_to_index(action)].item()
            next_state = apply_action(state, action, self.db)
            next_state = check_state_based_actions(next_state, self.db)
            node.children[action] = MCTSNode(next_state, prior)
        
        node.is_expanded = True
        return value
    
    def _add_dirichlet_noise(self, root: MCTSNode, alpha=0.3, epsilon=0.25):
        # Only used during self-play, not during evaluation
        legal = get_legal_actions(root.state, self.db)
        noise = np.random.dirichlet([alpha] * len(legal))
        # Will be applied when node is first expanded
        root._dirichlet_noise = noise
    
    def _terminal_value(self, state: GameState, perspective: int) -> float:
        winner = state.winner()
        if winner is None:
            return 0.0        # draw
        return 1.0 if winner == perspective else -1.0
```

---

### Step 7 — Self-Play Game Loop

```python
@dataclass
class Datapoint:
    state_vector:  list[float]
    mcts_policy:   list[float]   # visit distribution over full action space
    game_outcome:  float         # +1 win, -1 loss, 0 draw
    turn_number:   int
    phase:         str
    player:        int

def play_one_game(net: DuelMastersNet, db_conn, 
                  n_simulations: int = 400) -> list[Datapoint]:
    
    state = initialize_game(DECK_A, DECK_B, db_conn)
    mcts  = MCTS(net, db_conn, n_simulations)
    
    # Collect (state_vec, policy) pairs — outcome added after game ends
    trajectory: list[tuple[list[float], list[float], int, int, str]] = []
    
    while not state.is_terminal():
        perspective = state.current_player
        
        # MCTS thinks → returns visit distribution
        visit_dist = mcts.run(state)
        
        # Build full-size policy vector (zeros for illegal actions)
        policy_vec = [0.0] * ACTION_DIM
        for action, prob in visit_dist.items():
            policy_vec[action_to_index(action)] = prob
        
        # Encode state from current player's perspective
        state_vec = encode_state(state, perspective, db_conn)
        
        # Store trajectory step
        trajectory.append((
            state_vec.tolist(),
            policy_vec,
            perspective,
            state.turn_number,
            state.current_phase.name
        ))
        
        # Sample action (use temperature: greedy late game, exploratory early)
        temperature = 1.0 if state.turn_number < 10 else 0.1
        action = sample_action(visit_dist, temperature)
        
        # Apply action + SBAs
        state = apply_action(state, action, db_conn)
        state = check_state_based_actions(state, db_conn)
        state = resolve_triggered_effects(state, db_conn)
    
    # Game over — label all trajectory steps with outcome
    winner = state.winner()
    datapoints = []
    
    for state_vec, policy_vec, player, turn_num, phase in trajectory:
        if winner is None:
            outcome = 0.0
        else:
            outcome = 1.0 if winner == player else -1.0
        
        datapoints.append(Datapoint(
            state_vector  = state_vec,
            mcts_policy   = policy_vec,
            game_outcome  = outcome,
            turn_number   = turn_num,
            phase         = phase,
            player        = player
        ))
    
    return datapoints
```

---

### Step 8 — Training

```python
def train(net: DuelMastersNet, replay_buffer: deque, 
          optimizer, batch_size: int = 512, steps: int = 1000):
    
    net.train()
    
    for step in range(steps):
        # Sample random mini-batch from replay buffer
        batch = random.sample(replay_buffer, batch_size)
        
        state_vecs  = torch.tensor([d.state_vector  for d in batch])
        target_pi   = torch.tensor([d.mcts_policy   for d in batch])
        target_z    = torch.tensor([d.game_outcome   for d in batch]).unsqueeze(1)
        
        # Build legal masks — all actions valid during training
        # (we train on the full distribution, masking done at inference)
        legal_masks = torch.ones(batch_size, ACTION_DIM, dtype=torch.bool)
        
        policy, value = net(state_vecs, legal_masks)
        
        # Policy loss: cross-entropy between MCTS π and network p
        policy_loss = -(target_pi * torch.log(policy + 1e-8)).sum(dim=1).mean()
        
        # Value loss: MSE between network v and actual outcome z
        value_loss  = ((value - target_z) ** 2).mean()
        
        # L2 regularization
        l2_loss = sum(p.pow(2).sum() for p in net.parameters()) * 1e-4
        
        total_loss = policy_loss + value_loss + l2_loss
        
        optimizer.zero_grad()
        total_loss.backward()
        optimizer.step()
        
        if step % 100 == 0:
            print(f"Step {step}: policy={policy_loss:.4f} value={value_loss:.4f}")
```

---

## Generation 0 Run Script

Putting it all together:

```python
def run_generation_zero():
    
    db_conn = psycopg2.connect(DSN)
    
    # Random-weight net
    net = DuelMastersNet(state_dim=STATE_DIM, action_dim=ACTION_DIM)
    optimizer = torch.optim.Adam(net.parameters(), lr=0.001, weight_decay=1e-4)
    
    replay_buffer = deque(maxlen=100_000)
    
    print("=== GENERATION 0: Self-play with random weights ===")
    
    # Self-play phase — 100 games
    for game_num in tqdm(range(100), desc="Self-play games"):
        datapoints = play_one_game(net, db_conn, n_simulations=200)
        replay_buffer.extend(datapoints)
        
        if game_num % 10 == 0:
            print(f"  Game {game_num}: {len(datapoints)} datapoints, "
                  f"buffer size: {len(replay_buffer)}")
    
    print(f"\nTotal datapoints collected: {len(replay_buffer)}")
    print("Starting first training run...")
    
    # Training phase
    train(net, replay_buffer, optimizer, batch_size=512, steps=2000)
    
    # Save Gen 0 weights
    torch.save({
        "model_state": net.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "generation": 0,
        "buffer_size": len(replay_buffer),
    }, "checkpoints/gen_000.pt")
    
    print("Generation 0 complete. Saved to checkpoints/gen_000.pt")
    
    db_conn.close()
```

---

## Does Generation 0 Need a Neural Net?

Yes — and here is why it cannot be skipped:

A pure random mover (no net) generates **uniformly random datapoints**. The policy target `π` would be uniform across all legal actions. The value target `z` would be near-random wins and losses. The net would train on noise and learn nothing useful.

With a random-weight net inside MCTS, something different happens. Even with random weights, MCTS explores and backpropagates real game outcomes. The visit distribution `π` reflects which actions MCTS actually explored more — even if only slightly non-uniform. The value signal `z` is a real game outcome (+1/-1). After training on even this weak signal, Gen 1 is measurably better than Gen 0.

This is the AlphaZero insight: **the neural net and MCTS improve each other iteratively**. You need the net from day one, even when it's useless, because the training loop depends on it structurally.

---

## What to Expect from Generation 0

After 100 self-play games and 2000 training steps with random weights:

- Games will be incoherent — random charges, no attack patterns, frequent illegal move attempts (which your engine rejects)
- Average game length will be close to the turn cap (30 turns) because neither bot knows how to win efficiently
- Loss will decrease but remain high — the net is learning very weak signal
- Gen 1 will be slightly less random — it will start preferring higher-cost creatures and attacking more often than passing

This is normal. The improvement accelerates dramatically around Gen 5–10 once the net has seen enough real game outcomes to distinguish good positions from bad ones.

---

# user

> now make plan to build game engine

# assistant

Good timing — while scraping runs in background, let's plan the engine properly. This is the most critical piece of the entire project.

---

## The Core Design Principle

**The engine is a pure function.**

```
(GameState, Action) → GameState
```

No globals, no mutation, no side effects. Every action takes a state and returns a new state. This is non-negotiable because MCTS needs to branch from any state thousands of times without corrupting the original.

---

## What the Engine Actually Is

Five distinct systems that work together:

```
┌─────────────────────────────────────────────────────┐
│                   GAME ENGINE                        │
│                                                      │
│  1. State Manager      — represents everything       │
│  2. Phase Controller   — turn structure & flow       │
│  3. Action System      — what's legal, what happens  │
│  4. Effect System      — card effects & triggers     │
│  5. Rule Enforcer      — SBAs, win/loss conditions   │
└─────────────────────────────────────────────────────┘
```

Each one has a clear boundary. Let's go through them.

---

## System 1 — State Manager

Everything about the game at one instant in time. One large dataclass. Nothing else.

### What it holds

```
GameState
├── players[2]
│   ├── hand:         list[Card]
│   ├── deck:         list[Card]        (ordered, top = index 0)
│   ├── mana_zone:    list[ManaCard]    (card + is_tapped bool)
│   ├── battle_zone:  list[Creature]    (see below)
│   ├── shield_zone:  list[ShieldCard]  (card + is_revealed bool)
│   └── graveyard:    list[Card]
│
├── turn_info
│   ├── turn_number:      int
│   ├── active_player:    int (0 or 1)
│   ├── phase:            Phase (enum)
│   └── has_charged_mana: bool   (can only charge once per turn)
│
├── effect_stack
│   ├── pending_triggers: list[PendingEffect]
│   ├── active_continuous: list[ContinuousEffect]
│   └── until_eot_effects: list[UntilEOTEffect]
│
└── history
    └── last_n_actions: list[Action]   (for UI, debugging, replays)
```

### The Creature object deserves special attention

```
Creature
├── card:              Card            (base card data)
├── base_power:        int             (from card data)
├── current_power:     int             (after modifiers)
├── is_tapped:         bool
├── has_summoning_sickness: bool
├── entered_turn:      int             (which turn it entered)
├── evolution_base:    list[Card]      (cards underneath if evolution)
├── attached_cards:    list[Card]      (cross gear, aura, etc.)
├── temp_flags:        dict[str, bool] (cannot_attack, cannot_block, etc.)
└── power_modifiers:   list[PowerMod]  (source, amount, until_when)
```

`current_power` is always computed fresh — sum of `base_power` + all `power_modifiers`. Never store a computed value permanently because modifiers expire at end of turn.

### Key rules about state design

**Decks are ordered lists.** Top of deck = index 0. Drawing = pop from front. Shuffling = random.shuffle in place on the copy.

**Shield zones are face-down by default.** The engine knows what each shield is (needed for Shield Trigger detection) but the opponent representation should not expose this — important later for the neural net encoder which must only see what the player can see.

**Graveyard order matters** for some effects. Keep it ordered, newest on top.

---

## System 2 — Phase Controller

Manages turn flow. Queries `dm_game_phases` and `dm_phase_actions` from your DB.

### The phase sequence

```python
class Phase(Enum):
    TURN_START   = 0   # untap step (mandatory, no player actions)
    DRAW         = 1   # draw 1 card (mandatory from turn 2)
    CHARGE       = 2   # optional: put 1 card to mana face-down
    MAIN         = 3   # play cards
    ATTACK       = 4   # declare attackers one at a time
    BLOCK        = 5   # opponent responds to each attack
    BATTLE       = 6   # power comparison (if blocked)
    SHIELD_BREAK = 6   # happens during attack resolution, not a separate phase
    TURN_END     = 7   # end of turn triggers, effect expiry, discard if needed
```

### Phase transitions

The phase controller decides when to move to the next phase. The rules are:

```
TURN_START  → always auto-advance (untap all, resolve start-of-turn triggers)
DRAW        → always auto-advance (draw card, resolve on-draw triggers)
CHARGE      → player passes or charges, then advance
MAIN        → player passes (done playing cards) → advance to ATTACK
ATTACK      → player passes all attacks → advance to TURN_END
              OR during each attack: go through BLOCK → BATTLE sub-sequence
TURN_END    → resolve end-of-turn triggers → expire effects → advance turn
```

### The attack sub-sequence is its own mini-loop

```
Player declares attacker
        ↓
Resolve "on_attack" triggers (before block offered)
        ↓
Does attacker have "cannot be blocked"?
  YES → skip to shield break / direct attack
  NO  → offer block to opponent
            ↓
        Opponent has blocker(s) / guardman?
          YES → opponent must/may declare blocker
                    ↓
                Resolve BATTLE
                (power comparison + slayer + SBA check)
          NO  → proceed to shield break / direct attack
```

This sub-loop runs once per attacking creature. The ATTACK phase doesn't end until the active player passes without declaring any more attackers.

### How it queries your DB

```python
def get_phase_rules(phase: Phase, db_conn) -> list[PhaseAction]:
    return db_conn.query("""
        SELECT action_type, who, is_mandatory, sequence
        FROM dm_phase_actions
        WHERE phase_id = (
            SELECT id FROM dm_game_phases WHERE phase_name = %s
        )
        ORDER BY sequence
    """, (phase.name,))
```

The phase controller uses these rows to know: what must happen automatically, what is offered to the player, in what order.

---

## System 3 — Action System

Two parts: **what is legal** and **what happens when you do it**.

### Legal Action Generator

Called every time the engine needs to present choices — during MCTS simulation and during actual play.

```
get_legal_actions(state, db_conn) → list[Action]
```

Decision tree for each phase:

```
CHARGE phase:
  → any card in hand → CHARGE_MANA action
  → always → PASS action

MAIN phase:
  For each card in hand:
    → is creature AND has enough mana AND correct civilizations?
      → check Gravity Zero: cost becomes 0 if condition met
      → check Sympathy: cost reduced per matching creature
      → SUMMON action
    → is spell AND has enough mana AND correct civilizations?
      → CAST_SPELL action
  → always → PASS (end main phase)

ATTACK phase:
  For each creature in battle zone (my side):
    → not tapped?
    → no summoning sickness? (unless speed attacker)
    → no "cannot attack" flag?
      → ATTACK_PLAYER action
      → for each opponent creature: ATTACK_CREATURE action
  → always → PASS (end attack phase)

BLOCK phase (opponent's turn to respond):
  For each creature in battle zone (my side):
    → has blocker keyword?
      → DECLARE_BLOCKER action
    → has guardman keyword AND attacker is targeting player?
      → DECLARE_GUARDMAN action
  → always → PASS (don't block)

TARGET_SELECTION (during effect resolution):
  → depends entirely on the effect's target spec
  → filter valid targets by zone, type, controller constraints
```

### Action Executor

```
execute_action(state, action, db_conn) → GameState
```

One function per action type. Each returns a new state:

```python
def execute_summon(state, action, db_conn) -> GameState:
    new_state = state.copy()
    card = find_card(new_state.players[state.active_player].hand, action.card_id)
    
    # Pay cost — tap mana cards
    new_state = tap_mana_for_cost(new_state, card, action.mana_selection)
    
    # Move card from hand to battle zone
    new_state.players[state.active_player].hand.remove(card)
    creature = Creature(
        card=card,
        base_power=card.power,
        current_power=card.power,
        is_tapped=False,
        has_summoning_sickness=True,
        entered_turn=state.turn_info.turn_number
    )
    new_state.players[state.active_player].battle_zone.append(creature)
    
    # Queue ETB trigger if the card has one
    etb_effects = get_effects_by_trigger(card.id, "on_enter_battle_zone", db_conn)
    for effect in etb_effects:
        new_state.effect_stack.pending_triggers.append(
            PendingEffect(effect=effect, source_card=card, controller=state.active_player)
        )
    
    return new_state
```

Every action executor follows the same pattern:
1. Copy state
2. Do the mechanical thing (move cards, tap mana)
3. Queue any triggered effects
4. Return new state (do NOT resolve triggers here — that's System 4's job)

---

## System 4 — Effect System

The most complex system. Handles three distinct types of effects.

### Type 1 — Triggered Effects

Fire once when their trigger event occurs. Come from `card_effects` rows with a `trigger_event`.

```
Trigger events in your DB:
  on_enter_battle_zone   — ETB effects
  on_attack              — when this creature attacks
  on_break_shield        — when this creature breaks a shield
  on_destroy             — when this creature is destroyed
  on_leave_battle_zone   — when this leaves for any reason
  start_of_turn          — upkeep triggers
  end_of_turn            — cleanup triggers
  on_summon              — when summoned (before ETB)
  on_shield_trigger      — when pulled from shield
  on_draw                — when drawn from deck
  on_block               — when this creature blocks
  on_battle              — when this creature battles
```

After every action, the engine checks for newly queued triggers and resolves them:

```python
def resolve_pending_triggers(state, db_conn) -> GameState:
    while state.effect_stack.pending_triggers:
        trigger = state.effect_stack.pending_triggers.pop(0)
        
        # Is the source card still in the right zone to trigger?
        if not trigger_still_valid(trigger, state):
            continue
        
        # Is it optional? If yes, ask the controller
        if trigger.effect.is_optional:
            # In self-play: MCTS will explore both choices
            # In actual play: prompt player
            state.effect_stack.awaiting_choice = trigger
            return state
        
        # Execute the effect
        state = execute_effect(trigger, state, db_conn)
        
        # After each effect resolves, check SBAs
        state = check_state_based_actions(state, db_conn)
    
    return state
```

### Type 2 — Static/Continuous Effects

Active as long as the source card is in play. Recalculated fresh on every state read.

```
Examples:
  "All your Fire creatures get +1000 power"
  "Your opponent's creatures can't attack"
  "Creatures cost 1 less to summon"
```

These are never "resolved" — they are applied when you read the state:

```python
def compute_current_power(creature: Creature, state: GameState, db_conn) -> int:
    base = creature.base_power
    
    # Query all active static power modifiers from cards in play
    static_effects = get_active_static_effects(state, db_conn, "power_modify")
    
    for effect in static_effects:
        if effect_applies_to(effect, creature, state):
            if effect.effect_value.get("per_card_in"):
                # Power Attacker style: +X per card in zone
                zone = effect.effect_value["per_card_in"]
                civ  = effect.effect_value.get("civilization")
                count = count_cards_in_zone(state, zone, civ, effect.controller)
                base += effect.effect_value["per_card_amount"] * count
            else:
                base += effect.effect_value["amount"]
    
    return base
```

You call `compute_current_power()` fresh every time you need a creature's power. Never cache it.

### Type 3 — Replacement Effects

The trickiest type. These say "instead of X happening, Y happens."

```
Examples:
  "When this would be destroyed, banish it to the Abyss Zone instead"
  "When a card would be put into your graveyard, put it into your mana zone instead"
```

Replacement effects intercept the normal execution chain. Before executing any state change, check if a replacement effect applies:

```python
def move_to_graveyard(card, player, state, db_conn) -> GameState:
    # Check for replacement effects first
    replacements = get_replacement_effects(card, "move_to_graveyard", state, db_conn)
    
    if replacements:
        # Apply the replacement instead
        return apply_replacement(replacements[0], card, player, state, db_conn)
    
    # Normal execution
    new_state = state.copy()
    new_state.players[player].graveyard.insert(0, card)
    return new_state
```

### Effect Resolution requires target selection

Many effects have `effect_target` with constraints. When the engine needs a target, it pauses and asks:

```python
def resolve_effect_requiring_target(effect, state, db_conn):
    # Get valid targets based on effect_target spec
    valid_targets = get_valid_targets(effect.effect_target, state, db_conn)
    
    if not valid_targets:
        # Effect fizzles — no valid targets
        return state
    
    if len(valid_targets) == 1:
        # Auto-select if only one option
        return execute_with_target(effect, valid_targets[0], state, db_conn)
    
    # Multiple targets — need player input
    # In MCTS: expand a child node for each valid target
    # In real play: prompt player
    state.effect_stack.awaiting_target_selection = TargetRequest(
        effect=effect,
        valid_targets=valid_targets,
        count=effect.effect_target.get("count", 1),
        is_up_to=effect.effect_target.get("count_type") == "up_to"
    )
    return state
```

This "awaiting" state is how MCTS handles choices — each valid target becomes a separate branch to explore.

---

## System 5 — Rule Enforcer

Runs automatically after every single action. Never skipped.

### State-Based Actions

Directly from your `dm_state_based_actions` table. The 13 SBAs in priority order:

```python
def check_state_based_actions(state: GameState, db_conn) -> GameState:
    sbas = db_conn.query("""
        SELECT condition_json, effect_json, priority
        FROM dm_state_based_actions
        ORDER BY priority ASC
    """)
    
    changed = True
    while changed:
        changed = False
        for sba in sbas:
            if evaluate_condition(sba.condition_json, state):
                state = apply_sba_effect(sba.effect_json, state, db_conn)
                changed = True
                break  # restart from highest priority after any change
    
    return state
```

The `while changed` loop is important — one SBA firing can create a condition that triggers another SBA. Keep checking until the game state is stable.

### Win/Loss Detection

SBAs 703.4b and 703.4c handle the two loss conditions:
- Deck runs out → lose at the start of your next draw step when you can't draw
- Took a direct attack with 0 shields → lose immediately

Both set a `loser` flag on the state. The engine stops at any terminal state.

---

## The Full Action Resolution Chain

Every time any action happens, this exact sequence runs:

```
1. Validate action is legal
        ↓
2. Execute action (move cards, tap mana)
        ↓
3. Queue any triggered effects from the action
        ↓
4. Check State-Based Actions (loop until stable)
        ↓
5. Resolve pending triggers (may require target selection)
        ↓
6. After each trigger resolves → back to step 4
        ↓
7. Check win/loss conditions
        ↓
8. Return new GameState
```

This chain is called identically in MCTS simulation and in real game play. No special cases.

---

## The Hardest Parts to Implement Correctly

These will take most of your time. Know them upfront.

### Shield Trigger timing

When a shield is broken, before the active player continues:

```
Shield broken
    ↓
Reveal shield card
    ↓
Does it have Shield Trigger?
  YES → controller may cast it immediately for free
        BEFORE the attacker continues (even mid-multi-break)
        Cast it → resolve its effects → SBA check
        THEN attacker continues to next shield / direct attack
  NO  → goes to hand, attacker continues
```

Multi-break (Double Breaker breaking 2 shields) means this sub-loop runs twice. If the first Shield Trigger destroys the attacker, the second break still happens if the attacker survived at time of break (this is contentious — look up the exact rule in your dm_rules).

### Ninja Strike timing window

```
Player declares blocker
    ↓
BEFORE battle resolves:
  Does active player have a Ninja Strike creature in hand?
  Cost condition met?
    YES → may discard it from hand instead of battling normally
          The Ninja replaces the attacker in battle
```

This is an interrupt to the normal block sequence. The engine needs to offer it as an action at exactly the right moment.

### Mana tapping — civilization matching

When paying a cost, the player chooses which mana cards to tap. The engine must:

1. Check total mana count ≥ cost
2. Check that the combination of tapped cards covers all required civilizations
3. For multi-civilization cards (they count as either civilization)

This means mana selection is itself an action with sub-choices. For MCTS, generate all valid mana combinations as separate action variants — or simplify by auto-selecting the cheapest valid combination (greedy) during early development.

### Evolution creatures

```
Player attempts to summon evolution:
    ↓
Check: is there a valid base in battle zone?
  (correct race, correct card type)
  (Neo Evolution: must be a non-evolution of correct race)
    ↓
Player chooses which base creature to evolve onto
    ↓
Evolution sits on top of base
Base is kept underneath (accessible for race checks, some effects)
Evolution inherits: none of base's tap state, abilities
Evolution has: its own power, abilities, summoning sickness
    ↓
If evolution leaves play: base creature is also removed
  (unless effect says otherwise)
```

The `evolution_base` list in the Creature object handles this. Push new evolutions onto the front.

### "Until end of turn" effect expiry

Some effects last only until end of turn. They must expire at the right time:

```python
def expire_eot_effects(state: GameState) -> GameState:
    new_state = state.copy()
    
    # Remove all UntilEOT effects
    new_state.effect_stack.until_eot_effects = []
    
    # Recompute all creature powers (modifiers may have expired)
    for player in range(2):
        for creature in new_state.players[player].battle_zone:
            creature.current_power = compute_current_power(creature, new_state, db_conn)
    
    # SBA check — a creature may now be at 0 power after modifier expired
    new_state = check_state_based_actions(new_state, db_conn)
    
    return new_state
```

---

## Build Order

Do not build everything at once. Build in layers, each one playable and testable:

### Layer 0 — Skeleton (Week 1)
- `GameState` dataclass and all sub-objects
- `Phase` enum and phase transition logic
- Game initialization (shuffle decks, deal 5 shields, draw 5 cards)
- Basic legal action generation (no card effects, just structure)
- Terminal state detection

**Test:** Can you initialize a game and step through phases without crashing?

### Layer 1 — Vanilla Creatures (Week 2)
- Summon a vanilla creature (pay mana, move to battle zone, summoning sickness)
- Attack with a creature (tap it, check sickness)
- Battle resolution (compare power, destroy loser)
- Shield breaking (break shield, move to hand, check Shield Trigger placeholder)
- Direct attack (opponent has no blockers)
- SBA: creature at 0 power → destroy
- SBA: deck empty → lose
- SBA: direct attack with 0 shields → lose

**Test:** Play full games with vanilla creatures only. Verify win/loss conditions trigger correctly.

### Layer 2 — Spells (Week 3)
- Cast spell (pay mana, resolve effect, graveyard)
- Shield Trigger (detect, offer, cast for free)
- Effect target selection (for spells like Aqua Surfer)
- Mana civilization checking (correct civs to cast)

**Test:** Play games with creatures + spells. Verify Shield Triggers interrupt attack sequence.

### Layer 3 — Keywords (Week 4)
- Blocker / Guardman
- Double Breaker / Triple Breaker / World Breaker
- Speed Attacker (no summoning sickness)
- Power Attacker (query card_effects, recompute power)
- Slayer (destroy both regardless of power)
- Cannot be blocked (from card_effects static flag)

**Test:** Build test decks specifically featuring each keyword. Verify each one mechanically.

### Layer 4 — Triggered Effects (Week 5)
- ETB effects (draw, destroy, search, bounce)
- Attack triggers
- On-destroy triggers
- Static continuous effects (power boosts to all creatures)
- "Until end of turn" effect expiry

**Test:** Test each effect_action type individually using single-card test cases.

### Layer 5 — Advanced (Week 6+)
- Evolution creatures
- Ninja Strike
- Replacement effects
- Multi-civilization mana
- Cross gear / D2 Field (if using modern cards)
- Revolution Change / Invasion (if using those sets)

---

## Testing Strategy

The engine needs heavy unit testing. Wrong rules = wrong training data = useless AI.

### Unit tests per rule
```
test_sba_zero_power_destroys_creature()
test_sba_empty_deck_loses_on_draw()
test_sba_direct_attack_zero_shields_loses()
test_shield_trigger_interrupts_attack()
test_speed_attacker_no_summoning_sickness()
test_double_breaker_breaks_two_shields()
test_evolution_requires_valid_base()
test_ninja_strike_timing_window()
test_power_attacker_recomputes_on_mana_change()
test_until_eot_effect_expires_correctly()
```

### Simulation tests
Run 1000 random games and assert:
- No game exceeds 30 turns without triggering draw cap
- Every game ends with exactly one winner (or draw)
- No creature in battle zone ever has negative power
- Shield zones never exceed 5
- Mana paid always exactly equals card cost (civilization-valid)
- No card exists in two zones simultaneously

### Replay test
Record a complete game as a sequence of actions. Replay it from the initial state and verify the final state is identical. This catches any non-determinism.

---

## What You Need Before Starting

From your existing work:

```
Already done ✓
  dm_rules        — 725 rules for edge case reference
  dm_keywords     — 35 keywords with behavior flags
  dm_game_phases  — 10 phases
  dm_phase_actions — ordered actions per phase
  dm_state_based_actions — 13 SBAs
  card_effects    — LLM-parsed per-card ability rows (in progress)
  v_card_engine   — pre-joined view for fast card queries

Need to do before engine work:
  □ Define your starting card pool
    (recommend: 100-150 cards, 2-3 sets, vanilla + basic keywords only)
  □ Verify card_effects parse quality on those 150 cards
    (manually check any parse_confidence < 0.8)
  □ Define your fixed deck format
    (40 cards, max 4 copies — standard DM rules)
  □ Define your action space size
    (enumerate all possible action types × card slots = ACTION_DIM for the net)
```

The card pool decision is the most important one right now. Start narrow — 150 cards covering basic mechanics — and expand after the training loop is proven. Every card you add increases engine complexity and the chance of a rules bug corrupting your training data.

---

# user

> 1st create a folder and game engine code there step by step. 1st state manager, there should information about both player condition like hand, battlezone, mana zone etc. player 1 bot can not see some of own state like like deck ordering. Player 1 will know his deck contents 4 of x card 4 of y card 2 of z card but don't know after shuffle which card is where same of shield cards. Also player 1 can some of player 2 info like creature in battle zone or cards in mana zone. Also there are some effect that apply over all like "player can't use spell other that light civi"

# assistant

Now the CardDatabase — loads card definitions from PostgreSQL:Now write the test that validates everything works together without a DB:Two bugs: `is_replacement` is both a field and a method name on `CardEffect`, and `Optional[int]` without a default makes it a required field. Let me fix both files cleanly:**119/119 passed.** Copy to outputs:**119/119 tests passed.** Here's what was built and why each decision was made.

---

## File structure

```
dm_engine/
├── core/
│   ├── enums.py          — all enums and constants (no logic)
│   ├── cards.py          — CardDefinition, CardEffect, DeckDefinition (immutable)
│   ├── zones.py          — HandCard, ManaCard, ShieldCard, Creature, GraveyardCard
│   ├── global_effects.py — game-wide effects + GlobalEffectRegistry
│   ├── player_state.py   — one player's complete state + all zone queries
│   ├── state.py          — GameState master object + EffectStack + AttackContext
│   ├── observation.py    — information hiding layer (what each player sees)
│   └── initializer.py   — initialize_game() from two DeckDefinitions
├── db/
│   └── card_database.py  — loads CardDefinitions from PostgreSQL once at startup
└── tests/
    └── test_state_manager.py — 119 tests, no DB needed
```

---

## Key design decisions explained

**Information hiding is in `observation.py`, not `GameState`** — the engine always has full information (it needs it to enforce rules correctly — e.g. detecting Shield Triggers on face-down shields). The `Observation` class is built fresh when needed and is what the neural net and bot receive, never the raw `GameState`.

**`CardDefinition` is frozen and shared by pointer** — every `Creature`, `HandCard`, `ManaCard` etc. holds a reference to the same `CardDefinition` object in memory. When MCTS deepcopies a `GameState`, the card definitions are NOT copied — only the zone objects are. This makes branching fast.

**`current_power` is never stored on `Creature`** — `compute_power()` is always called fresh. This prevents stale values after a Power Attacker modifier expires or a global effect changes.

**`GlobalEffectRegistry` handles your Alcadeias example exactly** — `RESTRICT_SPELL_CIVILIZATION` with `allowed_civilizations={Light}` and `target_player=None` blocks both players from casting non-Light spells. Removing the effect when Alcadeias leaves the battle zone is one `remove_by_source(uid)` call.

**Deck composition is public, deck order is private** — `deck_composition` on `PlayerState` is set at game start and never changes (it's the card counts the player declared). The actual `deck` list order is only accessible via the engine's full `GameState`, never via the `Observation` the player sees.

---

# user

> can give me folder structure for those files

# assistant

Here is the current structure, plus the full planned structure for everything that comes next:

```
dm_engine/
│
├── __init__.py
│
├── core/                          ✅ DONE — data layer
│   ├── __init__.py
│   ├── enums.py                   ✅ all enums, phases, keywords, action types
│   ├── cards.py                   ✅ CardDefinition, CardEffect, DeckDefinition
│   ├── zones.py                   ✅ HandCard, ManaCard, ShieldCard, Creature, GraveyardCard
│   ├── global_effects.py          ✅ GlobalEffect, GlobalEffectRegistry
│   ├── player_state.py            ✅ PlayerState (all zones for one player)
│   ├── state.py                   ✅ GameState, TurnInfo, EffectStack, AttackContext
│   ├── observation.py             ✅ Observation (information hiding layer)
│   ├── initializer.py             ✅ initialize_game()
│   └── actions.py                 ✅ Action dataclass + all constructor functions
│
├── db/                            ✅ DONE — database layer
│   ├── __init__.py
│   └── card_database.py           ✅ CardDatabase (loads cards from PostgreSQL)
│
├── engine/                        ⬜ NEXT — game logic layer
│   ├── __init__.py
│   ├── action_generator.py        ⬜ get_legal_actions() — what moves are valid
│   ├── action_executor.py         ⬜ execute_action() — apply move, return new state
│   ├── effect_executor.py         ⬜ execute all card_effects (draw, destroy, etc.)
│   ├── sba_checker.py             ⬜ check_state_based_actions() — 13 SBAs from DB
│   ├── trigger_resolver.py        ⬜ resolve pending triggered effects in order
│   └── phase_controller.py        ⬜ advance phases, auto-resolve mandatory steps
│
├── effects/                       ⬜ NEXT — per-effect-action implementations
│   ├── __init__.py
│   ├── draw.py                    ⬜ draw N cards
│   ├── destroy.py                 ⬜ destroy creature(s), handle replacements
│   ├── bounce.py                  ⬜ return to hand
│   ├── search.py                  ⬜ search deck, reveal, add to hand
│   ├── mana_effects.py            ⬜ put_to_mana, cost_reduce, cost_increase
│   ├── summon_effects.py          ⬜ summon_free, put_to_battle_zone
│   ├── power_effects.py           ⬜ power_modify, power_fix
│   ├── shield_effects.py          ⬜ break_shield, put_to_shield, shield trigger resolve
│   └── keyword_effects.py         ⬜ give_keyword, grant to all, seal/unseal
│
├── bot/                           ⬜ AFTER ENGINE — bot layer
│   ├── __init__.py
│   ├── random_bot.py              ⬜ picks random legal action (Gen 0 baseline)
│   ├── mcts.py                    ⬜ MCTSNode, MCTS tree search
│   └── state_encoder.py           ⬜ encode GameState → float vector for neural net
│
├── neural/                        ⬜ AFTER BOT — neural network layer
│   ├── __init__.py
│   ├── network.py                 ⬜ DuelMastersNet (policy head + value head)
│   ├── trainer.py                 ⬜ training loop, loss function
│   └── replay_buffer.py           ⬜ experience replay buffer
│
├── selfplay/                      ⬜ AFTER NEURAL — training pipeline
│   ├── __init__.py
│   ├── game_runner.py             ⬜ run one self-play game, collect datapoints
│   ├── datapoint.py               ⬜ Datapoint dataclass (state, policy, outcome)
│   └── pipeline.py                ⬜ full Gen N → train → Gen N+1 loop
│
├── tests/                         ✅ PARTIAL
│   ├── __init__.py
│   ├── test_state_manager.py      ✅ 119 tests — state, zones, observation
│   ├── test_actions.py            ✅ 213 tests — actions, enums, mana rules
│   ├── test_action_generator.py   ⬜ legal move generation per phase
│   ├── test_action_executor.py    ⬜ move execution, SBAs, triggers
│   ├── test_effects.py            ⬜ each effect_action type
│   └── test_game_loop.py          ⬜ full game from start to terminal state
│
└── main.py                        ⬜ entry point — runs two bots against each other
```

---

**332 tests passing so far** across the two completed test files.

**Build order from here:**

```
1. engine/sba_checker.py       — SBAs from your dm_state_based_actions table
2. engine/action_generator.py  — legal moves per phase
3. engine/effect_executor.py   — executes card_effects rows
4. engine/action_executor.py   — ties it all together
5. engine/phase_controller.py  — turn flow
6. main.py                     — first runnable game
```

The `effects/` folder individual files get built alongside `effect_executor.py` — one file per `effect_action` type, each directly mapping to a row in your `card_effects` table.