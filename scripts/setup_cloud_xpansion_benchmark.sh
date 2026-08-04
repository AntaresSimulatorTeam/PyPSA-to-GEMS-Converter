#!/usr/bin/env bash
# Copyright (c) 2026, RTE (https://www.rte-france.com)
#
# One-shot cloud runner for Ubuntu 22.04:
#   setup env + Antares/Xpansion binaries, then run the Xpansion benchmark only.
#
#   cd PyPSA-to-GEMS-Converter
#   bash scripts/setup_cloud_xpansion_benchmark.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f "${REPO_ROOT}/dependencies.json" && -f "${REPO_ROOT}/pyproject.toml" ]] \
  || die "Run this from the cloned repo root (got ${REPO_ROOT})"

export PATH="${HOME}/.local/bin:${PATH}"

log "Installing system packages"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  ca-certificates curl tar git build-essential \
  libopenmpi3 openmpi-bin coinor-cbc

if ! command -v uv >/dev/null 2>&1; then
  log "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi
command -v uv >/dev/null 2>&1 || die "uv not on PATH; add \$HOME/.local/bin to PATH"

log "Syncing Python dependencies"
uv python install 3.11
uv sync --frozen --group dev

read_dep() {
  uv run python -c "import json; print(json.load(open('dependencies.json'))['$1'])"
}
ANTARES_VERSION="$(read_dep antares_version)"
XPANSION_VERSION="$(read_dep antares_xpansion_version)"

ANTARES_DIR="antares-${ANTARES_VERSION}-Ubuntu-22.04"
ANTARES_ARCHIVE="${ANTARES_DIR}.tar.gz"
ANTARES_URL="https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases/download/v${ANTARES_VERSION}/${ANTARES_ARCHIVE}"

XPANSION_DIR="antaresXpansion-${XPANSION_VERSION}-ubuntu-22.04"
XPANSION_ARCHIVE="${XPANSION_DIR}.tar.gz"
XPANSION_URL="https://github.com/AntaresSimulatorTeam/antares-xpansion/releases/download/v${XPANSION_VERSION}/${XPANSION_ARCHIVE}"

if [[ ! -d "${ANTARES_DIR}" ]]; then
  log "Downloading Antares Simulator v${ANTARES_VERSION}"
  curl -L -f --retry 3 --retry-delay 2 -o "${ANTARES_ARCHIVE}" "${ANTARES_URL}"
  tar -xzf "${ANTARES_ARCHIVE}"
  rm -f "${ANTARES_ARCHIVE}"
fi

if [[ ! -d "${XPANSION_DIR}" ]]; then
  log "Downloading Antares Xpansion v${XPANSION_VERSION}"
  curl -L -f --retry 3 --retry-delay 2 -o "${XPANSION_ARCHIVE}" "${XPANSION_URL}"
  tar -xzf "${XPANSION_ARCHIVE}"
  rm -f "${XPANSION_ARCHIVE}"
fi

[[ -x "${ANTARES_DIR}/bin/antares-solver" ]] || die "missing ${ANTARES_DIR}/bin/antares-solver"
[[ -x "${XPANSION_DIR}/antares-xpansion-launcher" ]] || die "missing ${XPANSION_DIR}/antares-xpansion-launcher"
command -v cbc >/dev/null 2>&1 || die "cbc not on PATH"

mkdir -p tmp/xpansion_benchmark_results logs
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG="logs/xpansion_all_${TIMESTAMP}.log"
RESULTS_CSV="tmp/xpansion_benchmark_results/xpansion_scenario_results.csv"
rm -f "${RESULTS_CSV}"

log "Running Xpansion benchmark (log: ${LOG})"
set +e
uv run pytest tests/local_benchmark/xpansion_benchmark.py \
  -v -s --log-cli-level=INFO --tb=short -rA \
  2>&1 | tee "${LOG}"
RC=${PIPESTATUS[0]}
set -e

log "Finished (exit ${RC})"
echo "Log:     ${LOG}"
echo "Results: ${RESULTS_CSV}"

if [[ -f "${RESULTS_CSV}" ]]; then
  uv run python - <<'PY'
import pandas as pd
from pathlib import Path
df = pd.read_csv(Path("tmp/xpansion_benchmark_results/xpansion_scenario_results.csv"))
cols = [c for c in [
    "study_name", "n_buses", "n_scenarios", "xpansion_status",
    "xpansion_total_time", "pypsa_total_time",
] if c in df.columns]
print(df[cols].to_string(index=False))
PY
fi

exit "${RC}"
