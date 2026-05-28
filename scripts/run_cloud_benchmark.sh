#!/usr/bin/env bash
# Cloud setup + full local benchmark run (2-scenario Xpansion first, then Antares modeler suite).
#
# Target: Ubuntu LTS on OVH (22.04 recommended — same as CI and Antares binaries).
# Uses apt-get (standard on Ubuntu; not a Debian-only script). From repository root:
#   chmod +x scripts/run_cloud_benchmark.sh
#   ./scripts/run_cloud_benchmark.sh
#
# Options:
#   --setup-only       Install deps and download Antares binaries only
#   --modeler-only     Run tests/local_benchmark/benchmark.py only
#   --xpansion-only    Run tests/local_benchmark/benchmark_xpansion.py only
#   --skip-download    Skip Antares / Xpansion download if dirs already exist
#   --fresh-results    Remove existing CSVs under tmp/benchmark_results/
#   --no-apt           Skip apt-get (if packages already installed)
#
# Required inputs (not downloaded by this script):
#   resources/test_files/*.nc  — all modeler benchmarks + 2-scenario France studies
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Ensure log/results dirs exist before nohup redirection (e.g. > tmp/benchmark_logs/cloud_run.log)
mkdir -p "${REPO_ROOT}/tmp/benchmark_logs" "${REPO_ROOT}/tmp/benchmark_results"

SETUP_ONLY=false
MODELER_ONLY=false
XPANSION_ONLY=false
SKIP_DOWNLOAD=false
FRESH_RESULTS=false
RUN_APT=true

for arg in "$@"; do
  case "${arg}" in
    --setup-only) SETUP_ONLY=true ;;
    --modeler-only) MODELER_ONLY=true ;;
    --xpansion-only) XPANSION_ONLY=true ;;
    --skip-download) SKIP_DOWNLOAD=true ;;
    --fresh-results) FRESH_RESULTS=true ;;
    --no-apt) RUN_APT=false ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: ${arg}" >&2
      exit 1
      ;;
  esac
done

if [[ "${MODELER_ONLY}" == true && "${XPANSION_ONLY}" == true ]]; then
  echo "Use at most one of --modeler-only and --xpansion-only." >&2
  exit 1
fi

RUN_MODELER=true
RUN_XPANSION=true
if [[ "${MODELER_ONLY}" == true ]]; then
  RUN_XPANSION=false
fi
if [[ "${XPANSION_ONLY}" == true ]]; then
  RUN_MODELER=false
fi

read_versions() {
  ANTARES_VERSION="$(uv run --python 3.11 python -c "import json; print(json.load(open('dependencies.json'))['antares_version'])")"
  XPANSION_VERSION="$(uv run --python 3.11 python -c "import json; print(json.load(open('dependencies.json'))['antares_xpansion_version'])")"
  XPANSION_BASE="${XPANSION_VERSION%%-rc*}"
  ANTARES_DIR="antares-${ANTARES_VERSION}-Ubuntu-22.04"
  XPANSION_DIR="antaresXpansion-${XPANSION_BASE}-ubuntu-22.04"
}

log() {
  echo "[$(date -Iseconds)] $*"
}

install_system_packages() {
  if [[ "${RUN_APT}" != true ]]; then
    log "Skipping apt-get (--no-apt)"
    return
  fi
  log "Installing Ubuntu packages via apt (build tools, OpenMPI, CBC)..."
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential \
    python3-dev \
    libgeos-dev \
    curl ca-certificates \
    libopenmpi3 openmpi-bin \
    coinor-cbc
}

install_python_deps() {
  # Pin 3.11 (project/CI target). Do not use system Python 3.12+ — many wheels are missing.
  local python_version="3.11"
  log "Installing Python ${python_version} and dependencies (uv sync --frozen --group dev)..."
  if ! command -v uv >/dev/null 2>&1; then
    log "uv not found; installing via official installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    source "${HOME}/.local/bin/env" 2>/dev/null || export PATH="${HOME}/.local/bin:${PATH}"
  fi
  uv python install "${python_version}"
  if [[ -d "${REPO_ROOT}/.venv" ]] && ! grep -q "3.11" "${REPO_ROOT}/.venv/pyvenv.cfg" 2>/dev/null; then
    log "Removing .venv (wrong Python version; need ${python_version})"
    rm -rf "${REPO_ROOT}/.venv"
  fi
  uv sync --frozen --group dev --python "${python_version}"
  log "Using: $(uv run --python "${python_version}" python -V)"
}

download_antares() {
  if [[ "${SKIP_DOWNLOAD}" == true && -d "${REPO_ROOT}/${ANTARES_DIR}" ]]; then
    log "Antares Simulator already present: ${ANTARES_DIR} (--skip-download)"
    return
  fi
  local archive="antares-${ANTARES_VERSION}-Ubuntu-22.04.tar.gz"
  local url="https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases/download/v${ANTARES_VERSION}/${archive}"
  log "Downloading Antares Simulator ${ANTARES_VERSION}..."
  curl -L -f -o "${archive}" "${url}"
  rm -rf "${ANTARES_DIR}"
  tar -xzf "${archive}"
  rm -f "${archive}"
  log "Antares extracted to ${ANTARES_DIR}"
}

download_xpansion() {
  if [[ "${SKIP_DOWNLOAD}" == true && -d "${REPO_ROOT}/${XPANSION_DIR}" ]]; then
    log "Antares Xpansion already present: ${XPANSION_DIR} (--skip-download)"
    return
  fi
  local archive="antaresXpansion-${XPANSION_BASE}-ubuntu-22.04.tar.gz"
  local url="https://github.com/AntaresSimulatorTeam/antares-xpansion/releases/download/v${XPANSION_VERSION}/${archive}"
  log "Downloading Antares Xpansion ${XPANSION_VERSION} (archive ${XPANSION_BASE})..."
  curl -L -f -o "${archive}" "${url}"
  rm -rf "${XPANSION_DIR}"
  tar -xzf "${archive}"
  rm -f "${archive}"
  log "Antares Xpansion extracted to ${XPANSION_DIR}"
}

verify_test_files() {
  local test_dir="${REPO_ROOT}/resources/test_files"
  if [[ ! -d "${test_dir}" ]]; then
    echo "Missing directory: ${test_dir}" >&2
    exit 1
  fi
  local missing=0
  if [[ "${RUN_MODELER}" == true ]]; then
    local count
    count="$(find "${test_dir}" -maxdepth 1 -name '*.nc' 2>/dev/null | wc -l)"
    if [[ "${count}" -lt 1 ]]; then
      echo "No .nc files in ${test_dir} (required for benchmark.py)." >&2
      missing=1
    fi
  fi
  if [[ "${RUN_XPANSION}" == true ]]; then
    for f in \
      "france_clusters_80_snapshots_168_period_one_week_2_scenarios.nc" \
      "france_clusters_50_snapshots_365_period_one_year_2_scenarios.nc"
    do
      if [[ ! -f "${test_dir}/${f}" ]]; then
        echo "Missing Xpansion input: ${test_dir}/${f}" >&2
        missing=1
      fi
    done
  fi
  if [[ "${missing}" -ne 0 ]]; then
    echo "Copy benchmark NetCDF files into resources/test_files/ before running." >&2
    exit 1
  fi
}

prepare_results_dir() {
  mkdir -p "${REPO_ROOT}/tmp/benchmark_results"
  mkdir -p "${REPO_ROOT}/tmp/benchmark_logs"
  if [[ "${FRESH_RESULTS}" == true ]]; then
    log "Removing previous benchmark CSVs (--fresh-results)"
    rm -f \
      "${REPO_ROOT}/tmp/benchmark_results/all_studies_results.csv" \
      "${REPO_ROOT}/tmp/benchmark_results/xpansion_benchmark_results.csv"
  fi
}

run_modeler_benchmark() {
  local log_file="${REPO_ROOT}/tmp/benchmark_logs/modeler_benchmark.log"
  log "Running Antares modeler benchmark (all studies) — log: ${log_file}"
  uv run pytest tests/local_benchmark/benchmark.py \
    -v -s --log-cli-level=INFO --tb=short -rA \
    2>&1 | tee "${log_file}"
}

run_xpansion_benchmark() {
  local log_file="${REPO_ROOT}/tmp/benchmark_logs/xpansion_benchmark.log"
  log "Running PyPSA vs Xpansion benchmark (2 France 2-scenario studies) — log: ${log_file}"
  uv run pytest tests/local_benchmark/benchmark_xpansion.py \
    -v -s --log-cli-level=INFO --tb=short -rA \
    2>&1 | tee "${log_file}"
}

main() {
  log "Repository: ${REPO_ROOT}"
  install_system_packages
  install_python_deps
  read_versions
  download_antares
  download_xpansion

  if [[ "${SETUP_ONLY}" == true ]]; then
    log "Setup complete (--setup-only)."
    exit 0
  fi

  verify_test_files
  prepare_results_dir

  # Xpansion first (2 large 2-scenario studies), then full modeler suite
  if [[ "${RUN_XPANSION}" == true ]]; then
    run_xpansion_benchmark
  fi
  if [[ "${RUN_MODELER}" == true ]]; then
    run_modeler_benchmark
  fi

  log "Done. Results:"
  log "  Xpansion: tmp/benchmark_results/xpansion_benchmark_results.csv"
  log "  Modeler:  tmp/benchmark_results/all_studies_results.csv"
  log "  Logs:     tmp/benchmark_logs/"
}

main "$@"
