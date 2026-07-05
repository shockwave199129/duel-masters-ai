#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BASE_DIR=$(cd "${SCRIPT_DIR}/../.." && pwd)
DATA_DIR="${BASE_DIR}/data/self_play"
MODEL_DIR="${BASE_DIR}/dm_engine/models"
LEAGUE_DIR="${MODEL_DIR}/league"
REPORT_DIR="${BASE_DIR}/data/reports"

load_env_file() {
    local env_file="$1"
    [[ -f "${env_file}" ]] || return 0
    while IFS= read -r line || [[ -n "${line}" ]]; do
        case "${line}" in
            ""|\#*)
                continue
                ;;
            export\ *)
                line="${line#export }"
                ;;
        esac

        [[ "${line}" == *"="* ]] || continue
        local key="${line%%=*}"
        local value="${line#*=}"
        key="${key//[[:space:]]/}"
        [[ -n "${key}" ]] || continue

        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"

        printf -v "${key}" '%s' "${value}"
        export "${key}"
    done < "${env_file}"
}

load_env_file "${BASE_DIR}/.env"
load_env_file "${BASE_DIR}/crawler/.env"

GENERATIONS=1   # how many generations to train
PROMOTION_THRESHOLD="0.52"
TRAIN_MODE="${TRAIN_MODE:-ppo}"
SELF_PLAY_PRESET="${SELF_PLAY_PRESET:-quick}"
SELF_PLAY_WORKERS="${SELF_PLAY_WORKERS:-1}"
SELF_PLAY_GAMES="${SELF_PLAY_GAMES:-}"
EVAL_GAMES="${EVAL_GAMES:-10}"
EVAL_OPPONENTS="${EVAL_OPPONENTS:-heuristic}"
CLEAN_START="${CLEAN_START:-0}"

GEN=0
PREV_MODEL=""   # empty → use the built-in gen-0 bot

if [[ "${CLEAN_START}" == "1" ]]; then
    rm -f "${DATA_DIR}"/gen*_v3_games.jsonl
    rm -f "${REPORT_DIR}"/gen*_eval.json
fi

cd "${BASE_DIR}"

while (( GEN < GENERATIONS )); do
    NEXT_GEN=$((GEN + 1))
    SELF_PLAY_OUTPUT="${DATA_DIR}/gen${GEN}_v3_games.jsonl"
    EVAL_JSON="${REPORT_DIR}/gen${NEXT_GEN}_eval.json"
    TRAIN_OUTPUT=""

    # -------------------------
    # 1. Self-play (standard = 100 games; each game samples 2 active DB decks)
    # Requires >= 2 active rows in training_decks (import via import_prebuilt_decks.py).
    # Every decision from player 0 and player 1 is recorded in that player's perspective.
    # -------------------------
    echo "=== Generation ${GEN} → self-play (gen ${NEXT_GEN} data) ==="
    SELF_PLAY_CMD=(
        python dm_engine/scripts/run_self_play.py
        --use-db-decks
        --output "${SELF_PLAY_OUTPUT}"
        --overwrite
        --record-encoder-version 3
    )
    if [[ -n "${SELF_PLAY_GAMES}" ]]; then
        SELF_PLAY_CMD+=(--games "${SELF_PLAY_GAMES}")
    fi
    if [[ "${SELF_PLAY_WORKERS}" != "1" ]]; then
        SELF_PLAY_CMD+=(--workers "${SELF_PLAY_WORKERS}")
    fi
    if [[ -n "${PREV_MODEL}" ]]; then
        SELF_PLAY_CMD+=(--model-path "${PREV_MODEL}")
    fi
    "${SELF_PLAY_CMD[@]}"

    # -------------------------
    # 2. Train new model (uses all P0 + P1 decisions, balanced 50/50 by default)
    # Optionally disable player balance for more even training (for very small datasets).
    # -------------------------
    echo "=== Training generation ${NEXT_GEN} model (${TRAIN_MODE}) ==="
    if [[ "${TRAIN_MODE}" == "ppo" ]]; then
        TRAIN_OUTPUT="${MODEL_DIR}/gen${NEXT_GEN}_v3_ppo.pt"
        python dm_engine/scripts/train_ppo.py \
            --input "${SELF_PLAY_OUTPUT}" \
            --output "${TRAIN_OUTPUT}" \
            --epochs 4 \
            --batch-size 32 \
            --lr 3e-4 \
            --hidden-size 384 \
            --num-blocks 6 \
            --dropout 0.10 \
            --seed $((42 + GEN))
    elif [[ "${TRAIN_MODE}" == "actor_critic" ]]; then
        TRAIN_OUTPUT="${MODEL_DIR}/gen${NEXT_GEN}_v3_action_critic.pt"
        python dm_engine/scripts/train_actor_critic.py \
            --input "${SELF_PLAY_OUTPUT}" \
            --output "${TRAIN_OUTPUT}" \
            --epochs 10 \
            --batch-size 64 \
            --lr 1e-3 \
            --hidden-size 384 \
            --num-blocks 6 \
            --dropout 0.10 \
            --seed $((42 + GEN))
    else
        TRAIN_OUTPUT="${MODEL_DIR}/gen${NEXT_GEN}_v3_action_score.pt"
        python dm_engine/scripts/train_action_score.py \
            --input "${SELF_PLAY_OUTPUT}" \
            --output "${TRAIN_OUTPUT}" \
            --epochs 20 \
            --batch-size 128 \
            --lr 5e-4 \
            --hidden-size 384 \
            --num-blocks 6 \
            --dropout 0.10 \
            --seed $((42 + GEN)) \
            --loss-mode pairwise \
            --no-balance-players
    fi

    mkdir -p "${LEAGUE_DIR}"
    cp "${TRAIN_OUTPUT}" "${LEAGUE_DIR}/gen${NEXT_GEN}.pt"
    PREV_MODEL="${TRAIN_OUTPUT}"

    # -------------------------
    # 3. Evaluate against heuristic bot before promotion
    # -------------------------
    echo "=== Evaluating generation ${NEXT_GEN} model ==="
    python dm_engine/scripts/eval_bots.py \
        --model-path "${TRAIN_OUTPUT}" \
        --opponents "${EVAL_OPPONENTS}" \
        --games "${EVAL_GAMES}" \
        --seed $((100 + GEN)) \
        --output "${EVAL_JSON}"

    WIN_RATE=$(python -c "import json,sys; data=json.load(open(sys.argv[1])); print(data['summary'][0]['win_rate'] if data['summary'] else 0.0)" "${EVAL_JSON}")
    echo "=== Eval win rate: ${WIN_RATE} (threshold ${PROMOTION_THRESHOLD}) ==="
    python -c "import sys; win=float(sys.argv[1]); threshold=float(sys.argv[2]); raise SystemExit(0 if win >= threshold else 1)" "${WIN_RATE}" "${PROMOTION_THRESHOLD}"

    # -------------------------
    # 4. Prepare for next loop
    # -------------------------
    GEN=$NEXT_GEN
done

echo "All done! Final model at: $PREV_MODEL"
