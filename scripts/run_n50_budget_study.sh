#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

PYTHON_BIN="${PYTHON_BIN:-python3}"

export NSEEDS="${NSEEDS:-300}"
export JOBS="${JOBS:-8}"
export N_NODES="${N_NODES:-50}"
export NET="${NET:-noisy}"
export EXTRA_CASES="${EXTRA_CASES:-CaseA_50_50 CaseB_80_20}"
export FIXED_BUDGETS="${FIXED_BUDGETS:-4 8 12 16}"
export QUAR_BUDGETS="${QUAR_BUDGETS:-4 8 12 16}"
export CP_EPOCH_MODE="${CP_EPOCH_MODE:-height}"
export CP_TIEBREAK="${CP_TIEBREAK:-none}"
export EPOCH_LEN_BLOCKS="${EPOCH_LEN_BLOCKS:-30}"

if [[ -z "${OUTDIR:-}" ]]; then
  export OUTDIR="${SCRIPT_DIR}/n50_budget_study_$(date +%Y%m%d_%H%M%S)"
fi

echo "[INFO] Running N=50 budget study"
echo "[INFO] outdir=${OUTDIR}"
echo "[INFO] n_seeds=${NSEEDS} jobs=${JOBS}"
echo "[INFO] cases=${EXTRA_CASES}"
echo "[INFO] fixed_budgets=${FIXED_BUDGETS}"
echo "[INFO] quar_budgets=${QUAR_BUDGETS}"

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/run_n50_budget_study.py"
