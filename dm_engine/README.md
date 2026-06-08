# Duel Masters Engine

`dm_engine` is a Python rules engine for Duel Masters simulations. It builds immutable-ish `GameState` snapshots, generates legal actions, executes one action at a time, and lets bots play by choosing from those legal actions.

The rule source of truth is `../Duel_Masters_rules.md`. When changing gameplay behavior, check the matching rule section first.

## Project Layout

- `core/` contains card definitions, zones, player state, game state, observations, enums, and game initialization.
- `engine/` contains legal action generation, action execution, phase control, battle/shield resolution, triggers/effects, state-based actions, and game running.
- `bot/` contains simple bot and observation encoding helpers.
- `db/` loads real card definitions from PostgreSQL.
- `decks/` contains prebuilt deck helpers, including JSON deck loading.
- `tests/` contains standalone test scripts. Each script can be run directly with Python.

The sibling package `../rules_ingest/` parses `Duel_Masters_rules.md` and ingests rules into PostgreSQL and ChromaDB.

## Basic Flow

The engine works like this:

1. Create or load two `DeckDefinition`s.
2. Resolve card IDs/names into `CardDefinition`s.
3. Call `initialize_game()` to create the starting `GameState`.
4. Generate legal actions with `get_legal_actions()`.
5. Apply one action with `execute_action()`.
6. Repeat, or use `run_game()` with a bot policy.

## Create A Game From Decks

```python
from core.cards import DeckDefinition
from core.initializer import initialize_game
from db.card_database import CardDatabase

db = CardDatabase("postgresql://user:pass@localhost/dm_db")
db.load()

deck_p0 = DeckDefinition(
    name="Player 0 Deck",
    owner="Player 0",
    card_counts={
        1: 4,
        2: 4,
        3: 4,
        4: 4,
        5: 4,
        6: 4,
        7: 4,
        8: 4,
        9: 4,
        10: 4,
    },
)

deck_p1 = DeckDefinition(
    name="Player 1 Deck",
    owner="Player 1",
    card_counts={
        11: 4,
        12: 4,
        13: 4,
        14: 4,
        15: 4,
        16: 4,
        17: 4,
        18: 4,
        19: 4,
        20: 4,
    },
)

deck_p0 = db.resolve_deck(deck_p0)
deck_p1 = db.resolve_deck(deck_p1)

state = initialize_game(deck_p0, deck_p1, first_player=0, seed=123)
```

Deck rules currently enforced:

- Main deck must contain exactly 40 cards.
- Main deck allows at most 4 copies of a card unless future card-specific exceptions are implemented.
- Counts must be positive.
- Every card must resolve to a `CardDefinition`.

## Use JSON Prebuilt Decks

For simulations, the easiest path is saving both player setups in JSON and loading them with `load_prebuilt_game_json()`.

Example `my_decks.json`:

```json
{
  "players": [
    {
      "name": "Fire Rush",
      "owner": "Player 0",
      "main": {
        "bolshack-dragon": 4,
        "aqua-hulcus": 4
      },
      "hyperspatial": {
        "psychic-creature-slug": 1
      },
      "ultra_gr": {
        "gr-creature-a": 2,
        "gr-creature-b": 2,
        "gr-creature-c": 2,
        "gr-creature-d": 2,
        "gr-creature-e": 2,
        "gr-creature-f": 2
      },
      "start_battle_zone": [
        "forbidden-sealed-x"
      ]
    },
    {
      "name": "Water Control",
      "owner": "Player 1",
      "main": {
        "aqua-hulcus": 4,
        "corile": 4
      }
    }
  ]
}
```

Then load it:

```python
from db.card_database import CardDatabase
from decks.prebuilt import load_prebuilt_game_json

db = CardDatabase("postgresql://user:pass@localhost/dm_db")
db.load()

state = load_prebuilt_game_json(
    "my_decks.json",
    db,
    first_player=0,
    seed=123,
)
```

JSON card keys can be card slugs, exact card names, or numeric card IDs as strings.

Extra-zone validation follows the rules:

- Hyperspatial Zone: up to 8 cards, max 4 copies.
- Ultra GR / Gacharange Zone: exactly 12 cards when used, max 2 copies.
- Starting Battle Zone: currently supports one starting set/card.

## Run Bots

Bots do not receive decks directly. They receive the current state and choose one legal action.

```python
from bot.random_bot import RandomBot
from decks.prebuilt import load_prebuilt_game_json
from engine.game_runner import run_game

state = load_prebuilt_game_json("my_decks.json", db, first_player=0, seed=123)

bot0 = RandomBot(seed=1)
bot1 = RandomBot(seed=2)

def policy(state, actions):
    bot = bot0 if state.active_player == 0 else bot1
    return bot.rng.choice(actions)

final_state = run_game(state, policy, db=db, max_steps=1000)
```

For a quick DB-free simulation, use the demo decks:

```python
from bot.random_bot import RandomBot
from core.initializer import initialize_game
from decks.prebuilt import make_demo_decks
from engine.game_runner import run_game

deck_p0, deck_p1 = make_demo_decks()
state = initialize_game(deck_p0, deck_p1, first_player=0, seed=7)

bot = RandomBot(seed=1)
final_state = run_game(
    state,
    policy=lambda state, actions: bot.rng.choice(actions),
    max_steps=100,
)
```

## Run The Gen 0 Neural Bot

The generation-0 neural bot uses a five-hidden-layer PyTorch model from
`bot.neural_model.ActionScoreNet`. It is not trained yet; PyTorch initializes
the weights and biases randomly. It can still play games because the engine
generates legal actions and the bot scores only those legal actions.

Install dependencies from the project root:

```bash
python -m pip install -r requirements.txt
```

Make sure `crawler/.env` contains `DATABASE_URL`, because the script loads card
definitions from PostgreSQL before loading the prebuilt decks:

```bash
DATABASE_URL=postgresql://user:pass@localhost:5432/dm_db
```

Run neural bot vs random bot with the default deck file
`dm_engine/decks/prebuilt_game.json`:

```bash
python dm_engine/scripts/play_neural_game.py \
  --mode neural-vs-random \
  --max-steps 1000
```

Run neural bot vs neural bot:

```bash
python dm_engine/scripts/play_neural_game.py \
  --mode neural-vs-neural \
  --max-steps 1000
```

Save a player-friendly step log to a text file:

```bash
python dm_engine/scripts/play_neural_game.py \
  --model-path dm_engine/models/gen1_v2_action_score.pt \
  --mode neural-vs-neural \
  --report-path data/reports/gen1_v2_game.txt
```

Use a different prebuilt game JSON:

```bash
python dm_engine/scripts/play_neural_game.py \
  --deck-json dm_engine/decks/prebuilt_game.json \
  --mode neural-vs-random
```

Useful flags:

- `--epsilon 0.05`: random exploration rate. Use `0.0` for fully greedy action selection.
- `--model-path path/to/model.pt`: load saved neural-network weights.
- `--first-player 0`: choose which player starts.
- `--seed 1`: make shuffle and bot choices reproducible. Omit it for a new random game each run.
- `--max-steps 1000`: stop long games after this many legal actions.
- `--show-steps`: print readable action-by-action output for non-technical review.
- `--report-path path.txt`: save the same readable action log to a text file.

## Manual Step Execution

Use this when debugging action generation or a specific rule interaction:

```python
from engine.action_generator import get_legal_actions
from engine.action_executor import execute_action

actions = get_legal_actions(state, db)
action = actions[0]
next_state = execute_action(state, action, db=db)
```

`execute_action()` returns a copied/updated state and then checks state-based actions.

## Observations For AI

Use `Observation` or the v2 encoders when feeding a bot/model. These hide
private information such as the opponent hand, hidden shield contents, exact deck
order, and opponent deck composition.

```python
from bot.state_encoder import encode_observation_v2

features = encode_observation_v2(state, perspective=0)
```

Actions are encoded with `bot.action_encoder.encode_action_v2()`. The neural bot
concatenates:

```text
encode_observation_v2(state, player) + encode_action_v2(action, state, db)
```

and feeds that vector into `ActionScoreNet`, which returns one score for that
legal action. v2 uses aggregated/bucketed state features and compact action
metadata rather than raw card-ID slots.

## Training The Neural Bot

The v2 reinforcement-learning loop records all legal actions for each decision,
stores the chosen action index, and trains `ActionScoreNet` using blended targets:
final win/loss plus a bounded heuristic score for non-terminal signal.

Run v2 gen-0 neural-vs-neural games and save every decision:

```bash
python dm_engine/scripts/run_self_play.py \
  --games 50 \
  --output data/self_play/gen0_v2_games.jsonl \
  --overwrite
```

You can also use presets:

```bash
python dm_engine/scripts/run_self_play.py --preset quick --overwrite     # 50 games
python dm_engine/scripts/run_self_play.py --preset standard --overwrite  # 100 games
python dm_engine/scripts/run_self_play.py --preset large --overwrite     # 500 games
```

Self-play randomizes first player and swaps the two deck seats between games by
default to reduce Player 0 / Deck 1 bias. Use `--fixed-seating` only when you
want reproducible old behavior where Player 0 always uses the first deck and
starts first.

The JSONL file contains one v2 training row for each decision:

```text
state_features
legal_action_features
chosen_index
policy_target
legal_actions
player_to_act
final_winner
value_target
heuristic_target
blended_target
```

The v2 trainer flattens each legal-action set into action-score examples. The
chosen action receives `blended_target`; non-chosen legal actions receive a lower
heuristic target. The default blend favors terminal outcome:

```text
blended_target = 0.65 * value_target + 0.35 * heuristic_target
```

`value_target` is `+1` if the player who made the decision won, `-1` if they
lost, and `0` for unfinished training games.

Duel Masters games are treated as win/loss games. In self-play, reaching
`--max-steps` does not create a draw; it is recorded as unfinished training data
with `value_target = 0.0`.

Train gen 1 from the recorded decisions:

```bash
python dm_engine/scripts/train_action_score.py \
  --input data/self_play/gen0_v2_games.jsonl \
  --output dm_engine/models/gen1_v2_action_score.pt \
  --epochs 10
```

Then run the trained model:

```bash
python dm_engine/scripts/play_neural_game.py \
  --model-path dm_engine/models/gen1_v2_action_score.pt \
  --mode neural-vs-neural \
  --max-steps 1000
```

Recommended training milestones:

1. Record 50 gen-0 v2 neural-vs-neural games for a quick check.
2. Train `ActionScoreNet` from v2 legal-action rows.
3. Run `NeuralBot` with `dm_engine/models/gen1_v2_action_score.pt`.
4. Record 100, then 500+ better self-play games from the latest model.
5. Add policy/value heads and MCTS only after the v2 pipeline and engine are stable.

## Run Tests

Run one test:

```bash
python dm_engine/tests/test_prebuilt_decks.py
```

Run all engine tests:

```bash
for f in dm_engine/tests/test_*.py; do python "$f" || exit 1; done
```

The tests are standalone scripts, not pytest tests.

## Current Limitations

The engine is still incomplete. Important known gaps include:

- Full simultaneous multi-shield declaration batching.
- Card-specific S-Back condition parsing.
- Real G-Strike and detailed effect text execution.
- More effect actions such as return-to-hand, tap, mana placement, and shield placement.
- Command seal-removal rules.
- Evolution reconstruction when only the top card leaves.
- More complete handling for special starting Battle Zone sets.

When adding these, write focused rule tests and cite the relevant rule numbers in comments or test names where useful.
