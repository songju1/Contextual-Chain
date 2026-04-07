#!/usr/bin/env bash
set -euo pipefail

PY="${PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

OUT_BASE="${OUT_BASE:-extB_caseB_validation_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-${SCRIPT_DIR}/${OUT_BASE}}"
mkdir -p "${OUT_DIR}"

NSEEDS="${NSEEDS:-200}"
SEED_START="${SEED_START:-0}"
JOBS="${JOBS:-8}"
EPOCH_LEN="${EPOCH_LEN:-30}"
CP_EPOCH_MODE="${CP_EPOCH_MODE:-height}"
CP_TIEBREAK="${CP_TIEBREAK:-none}"
EPOCH_LEN_S="${EPOCH_LEN_S:-}"
BLOCK_BYTES_EST="${BLOCK_BYTES_EST:-250}"
L_SCORE_WINDOW="${L_SCORE_WINDOW:-40}"
K_CONVERGE="${K_CONVERGE:-30}"
VARIANTS_MODE="${VARIANTS_MODE:-both}"
ATTACKER_BUDGETS="${ATTACKER_BUDGETS:-1 2 4 8 16}"
PROOF_MEM_CELLS="${PROOF_MEM_CELLS:-16384}"
PROOF_CELLS_PER_CP="${PROOF_CELLS_PER_CP:-2048}"
PROOF_CELL_BYTES="${PROOF_CELL_BYTES:-32}"
PROOF_TICK_S="${PROOF_TICK_S:-5.0}"
PROOF_TRIGGER_MODE="${PROOF_TRIGGER_MODE:-quarantine_or_rejoin}"
PROOF_TRIGGER_Q_FRAC="${PROOF_TRIGGER_Q_FRAC:-0.25}"
PROOF_CHALLENGE_MODE="${PROOF_CHALLENGE_MODE:-uniform_active}"
PROOF_CHALLENGE_K="${PROOF_CHALLENGE_K:-2}"
TAG="${TAG:-caseB}"

# shellcheck disable=SC2206
BUDGET_ARGS=( ${ATTACKER_BUDGETS} )

CMD=(
  "${PY}" "${SCRIPT_DIR}/rerun_proof_context_caseB_budget_sensitive.py"
  --outdir "${OUT_DIR}"
  --tag "${TAG}"
  --n-seeds "${NSEEDS}"
  --seed-start "${SEED_START}"
  --epoch-len "${EPOCH_LEN}"
  --cp-epoch-mode "${CP_EPOCH_MODE}"
  --cp-tiebreak "${CP_TIEBREAK}"
  --block-bytes-est "${BLOCK_BYTES_EST}"
  --L-score-window "${L_SCORE_WINDOW}"
  --K-converge "${K_CONVERGE}"
  --jobs "${JOBS}"
  --variants-mode "${VARIANTS_MODE}"
  --attacker-budget-contexts "${BUDGET_ARGS[@]}"
  --proof-mem-cells "${PROOF_MEM_CELLS}"
  --proof-cells-per-cp "${PROOF_CELLS_PER_CP}"
  --proof-cell-bytes "${PROOF_CELL_BYTES}"
  --proof-tick-s "${PROOF_TICK_S}"
  --proof-trigger-mode "${PROOF_TRIGGER_MODE}"
  --proof-trigger-q-frac "${PROOF_TRIGGER_Q_FRAC}"
  --proof-challenge-mode "${PROOF_CHALLENGE_MODE}"
  --proof-challenge-k "${PROOF_CHALLENGE_K}"
)

if [[ -n "${EPOCH_LEN_S}" ]]; then
  CMD+=( --epoch-len-s "${EPOCH_LEN_S}" )
fi

echo "[INFO] Running Extension-B validation on CaseB noisy only"
echo "[INFO] outdir=${OUT_DIR}"
echo "[INFO] n_seeds=${NSEEDS} jobs=${JOBS} budgets=${ATTACKER_BUDGETS}"

"${CMD[@]}"

echo "[DONE] Extension-B CaseB validation complete"
echo "[DONE] Results in: ${OUT_DIR}"
