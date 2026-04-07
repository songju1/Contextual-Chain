#!/usr/bin/env bash
# Validation bundle for the paper's final configuration.
# Runs:
#   1) scaling_caseA_noisy with 1000 seeds
#   2) sticky-free checkpoint comparison (height vs time) on main_core
#   3) Extension-B CaseB noisy validation

set -euo pipefail

PY="${PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# -----------------------------
# Toggle individual stages
# -----------------------------
RUN_SCALING="${RUN_SCALING:-1}"
RUN_CKPT_COMPARE="${RUN_CKPT_COMPARE:-1}"
RUN_EXTB_CASEB="${RUN_EXTB_CASEB:-1}"

# -----------------------------
# Common settings
# -----------------------------
JOBS="${JOBS:-8}"
BLOCK_BYTES_EST="${BLOCK_BYTES_EST:-250}"
L_SCORE_WINDOW="${L_SCORE_WINDOW:-40}"
K_CONVERGE="${K_CONVERGE:-30}"

OUT_BASE="${OUT_BASE:-validation_bundle_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-${SCRIPT_DIR}/${OUT_BASE}}"
mkdir -p "${OUT_DIR}"

# -----------------------------
# 1) Scaling validation
# -----------------------------
SCALING_SEEDS="${SCALING_SEEDS:-1000}"
SCALING_SEED_START="${SCALING_SEED_START:-0}"
SCALING_EPOCH_LEN="${SCALING_EPOCH_LEN:-30}"
SCALING_CP_EPOCH_MODE="${SCALING_CP_EPOCH_MODE:-height}"
SCALING_TAG="${SCALING_TAG:-scaling1000_stickynone}"

# -----------------------------
# 2) Checkpoint comparison
# -----------------------------
CKPT_SEEDS="${CKPT_SEEDS:-1000}"
CKPT_SEED_START="${CKPT_SEED_START:-0}"
CKPT_EPOCH_LEN_BLOCKS="${CKPT_EPOCH_LEN_BLOCKS:-30}"
TIME_EPOCH_LEN_S="${TIME_EPOCH_LEN_S:-900}"
CKPT_TAG_PREFIX="${CKPT_TAG_PREFIX:-ckpt_compare_stickynone}"

# -----------------------------
# 3) Extension-B CaseB validation
# -----------------------------
EXTB_CASEB_SEEDS="${EXTB_CASEB_SEEDS:-200}"
EXTB_CASEB_SEED_START="${EXTB_CASEB_SEED_START:-0}"
EXTB_CASEB_EPOCH_LEN="${EXTB_CASEB_EPOCH_LEN:-30}"
EXTB_CASEB_CP_EPOCH_MODE="${EXTB_CASEB_CP_EPOCH_MODE:-height}"
EXTB_CASEB_CP_TIEBREAK="${EXTB_CASEB_CP_TIEBREAK:-none}"
EXTB_CASEB_EPOCH_LEN_S="${EXTB_CASEB_EPOCH_LEN_S:-}"
EXTB_CASEB_VARIANTS_MODE="${EXTB_CASEB_VARIANTS_MODE:-both}"
EXTB_CASEB_ATTACKER_BUDGETS="${EXTB_CASEB_ATTACKER_BUDGETS:-1 2 4 8 16}"
EXTB_CASEB_PROOF_MEM_CELLS="${EXTB_CASEB_PROOF_MEM_CELLS:-16384}"
EXTB_CASEB_PROOF_CELLS_PER_CP="${EXTB_CASEB_PROOF_CELLS_PER_CP:-2048}"
EXTB_CASEB_PROOF_CELL_BYTES="${EXTB_CASEB_PROOF_CELL_BYTES:-32}"
EXTB_CASEB_PROOF_TICK_S="${EXTB_CASEB_PROOF_TICK_S:-5.0}"
EXTB_CASEB_PROOF_TRIGGER_MODE="${EXTB_CASEB_PROOF_TRIGGER_MODE:-quarantine_or_rejoin}"
EXTB_CASEB_PROOF_TRIGGER_Q_FRAC="${EXTB_CASEB_PROOF_TRIGGER_Q_FRAC:-0.25}"
EXTB_CASEB_PROOF_CHALLENGE_MODE="${EXTB_CASEB_PROOF_CHALLENGE_MODE:-uniform_active}"
EXTB_CASEB_PROOF_CHALLENGE_K="${EXTB_CASEB_PROOF_CHALLENGE_K:-2}"
EXTB_CASEB_TAG="${EXTB_CASEB_TAG:-caseB_validation}"

# -----------------------------
# Helpers
# -----------------------------
need_file() {
  local f="$1"
  if [[ ! -f "${SCRIPT_DIR}/${f}" ]]; then
    echo "[ERROR] Required file not found: ${SCRIPT_DIR}/${f}" >&2
    exit 1
  fi
}

banner() {
  echo
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

need_file "rerun_paper_experiments_v4.py"
need_file "sim_context_chain_v4.py"

if [[ "${RUN_EXTB_CASEB}" == "1" ]]; then
  need_file "run_extB_caseB_validation.sh"
  need_file "rerun_proof_context_caseB_budget_sensitive.py"
  need_file "sim_context_chain_extB_budget_sensitive.py"
fi

# -----------------------------
# 1) Scaling validation
# -----------------------------
if [[ "${RUN_SCALING}" == "1" ]]; then
  banner "[1/3] scaling_caseA_noisy with 1000 seeds"
  SCALING_OUT="${OUT_DIR}/scaling_caseA_noisy_1000"
  mkdir -p "${SCALING_OUT}"

  CMD=(
    "${PY}" "${SCRIPT_DIR}/rerun_paper_experiments_v4.py"
    --suite scaling_caseA_noisy
    --outdir "${SCALING_OUT}"
    --tag "${SCALING_TAG}"
    --n-seeds "${SCALING_SEEDS}"
    --seed-start "${SCALING_SEED_START}"
    --epoch-len "${SCALING_EPOCH_LEN}"
    --cp-epoch-mode "${SCALING_CP_EPOCH_MODE}"
    --cp-tiebreak none
    --block-bytes-est "${BLOCK_BYTES_EST}"
    --L-score-window "${L_SCORE_WINDOW}"
    --K-converge "${K_CONVERGE}"
    --jobs "${JOBS}"
  )

  echo "[INFO] outdir=${SCALING_OUT}"
  echo "[INFO] seeds=${SCALING_SEEDS} jobs=${JOBS}"
  "${CMD[@]}"
fi

# -----------------------------
# 2) Sticky-free checkpoint comparison
# -----------------------------
if [[ "${RUN_CKPT_COMPARE}" == "1" ]]; then
  banner "[2/3] sticky-free checkpoint comparison (height vs time)"
  CKPT_OUT="${OUT_DIR}/checkpoint_compare"
  mkdir -p "${CKPT_OUT}"

  HEIGHT_OUT="${CKPT_OUT}/height"
  mkdir -p "${HEIGHT_OUT}"
  CMD_HEIGHT=(
    "${PY}" "${SCRIPT_DIR}/rerun_paper_experiments_v4.py"
    --suite main_core
    --outdir "${HEIGHT_OUT}"
    --tag "${CKPT_TAG_PREFIX}_height"
    --n-seeds "${CKPT_SEEDS}"
    --seed-start "${CKPT_SEED_START}"
    --epoch-len "${CKPT_EPOCH_LEN_BLOCKS}"
    --cp-epoch-mode height
    --cp-tiebreak none
    --block-bytes-est "${BLOCK_BYTES_EST}"
    --L-score-window "${L_SCORE_WINDOW}"
    --K-converge "${K_CONVERGE}"
    --jobs "${JOBS}"
  )
  echo "[INFO] height outdir=${HEIGHT_OUT}"
  "${CMD_HEIGHT[@]}"

  TIME_OUT="${CKPT_OUT}/time"
  mkdir -p "${TIME_OUT}"
  CMD_TIME=(
    "${PY}" "${SCRIPT_DIR}/rerun_paper_experiments_v4.py"
    --suite main_core
    --outdir "${TIME_OUT}"
    --tag "${CKPT_TAG_PREFIX}_time"
    --n-seeds "${CKPT_SEEDS}"
    --seed-start "${CKPT_SEED_START}"
    --epoch-len "${CKPT_EPOCH_LEN_BLOCKS}"
    --cp-epoch-mode time
    --cp-tiebreak none
    --block-bytes-est "${BLOCK_BYTES_EST}"
    --L-score-window "${L_SCORE_WINDOW}"
    --K-converge "${K_CONVERGE}"
    --jobs "${JOBS}"
  )
  if [[ -n "${TIME_EPOCH_LEN_S}" ]]; then
    CMD_TIME+=( --epoch-len-s "${TIME_EPOCH_LEN_S}" )
  fi
  echo "[INFO] time outdir=${TIME_OUT}"
  echo "[INFO] time epoch_len_s=${TIME_EPOCH_LEN_S:-<omitted>}"
  "${CMD_TIME[@]}"
fi

# -----------------------------
# 3) Extension-B CaseB validation
# -----------------------------
if [[ "${RUN_EXTB_CASEB}" == "1" ]]; then
  banner "[3/3] Extension-B CaseB noisy validation"
  EXTB_OUT="${OUT_DIR}/extB_caseB"
  mkdir -p "${EXTB_OUT}"

  EXTB_ENV=(
    OUT_DIR="${EXTB_OUT}"
    NSEEDS="${EXTB_CASEB_SEEDS}"
    SEED_START="${EXTB_CASEB_SEED_START}"
    JOBS="${JOBS}"
    EPOCH_LEN="${EXTB_CASEB_EPOCH_LEN}"
    CP_EPOCH_MODE="${EXTB_CASEB_CP_EPOCH_MODE}"
    CP_TIEBREAK="${EXTB_CASEB_CP_TIEBREAK}"
    BLOCK_BYTES_EST="${BLOCK_BYTES_EST}"
    L_SCORE_WINDOW="${L_SCORE_WINDOW}"
    K_CONVERGE="${K_CONVERGE}"
    VARIANTS_MODE="${EXTB_CASEB_VARIANTS_MODE}"
    ATTACKER_BUDGETS="${EXTB_CASEB_ATTACKER_BUDGETS}"
    PROOF_MEM_CELLS="${EXTB_CASEB_PROOF_MEM_CELLS}"
    PROOF_CELLS_PER_CP="${EXTB_CASEB_PROOF_CELLS_PER_CP}"
    PROOF_CELL_BYTES="${EXTB_CASEB_PROOF_CELL_BYTES}"
    PROOF_TICK_S="${EXTB_CASEB_PROOF_TICK_S}"
    PROOF_TRIGGER_MODE="${EXTB_CASEB_PROOF_TRIGGER_MODE}"
    PROOF_TRIGGER_Q_FRAC="${EXTB_CASEB_PROOF_TRIGGER_Q_FRAC}"
    PROOF_CHALLENGE_MODE="${EXTB_CASEB_PROOF_CHALLENGE_MODE}"
    PROOF_CHALLENGE_K="${EXTB_CASEB_PROOF_CHALLENGE_K}"
    TAG="${EXTB_CASEB_TAG}"
  )
  if [[ -n "${EXTB_CASEB_EPOCH_LEN_S}" ]]; then
    EXTB_ENV+=( EPOCH_LEN_S="${EXTB_CASEB_EPOCH_LEN_S}" )
  fi

  echo "[INFO] outdir=${EXTB_OUT}"
  echo "[INFO] seeds=${EXTB_CASEB_SEEDS} jobs=${JOBS} budgets=${EXTB_CASEB_ATTACKER_BUDGETS}"
  env "${EXTB_ENV[@]}" "${SCRIPT_DIR}/run_extB_caseB_validation.sh"
fi

banner "Validation bundle complete"
echo "[DONE] Results root: ${OUT_DIR}"
