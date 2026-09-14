#!/usr/bin/env bash
#
# Copyright (c) 2026, RTE (https://www.rte-france.com)
#
# See AUTHORS.txt
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# SPDX-License-Identifier: MPL-2.0
#
# This file is part of the Antares project.
#
# One-shot environment setup: clone the repo, run this script, and everything needed
# to run tests/local_benchmark/benchmark_pypsa_vs_xpansion.py is in place --
# Python deps (uv sync, including xpress), system deps (openmpi, coinor-cbc), and the
# Antares Simulator + Antares Xpansion binaries (downloaded at the versions pinned in
# dependencies.json, same source/layout as .github/workflows/run-e2e-tests.yml).
#
# Usage:
#   ./setup_environment.sh
#
# Safe to re-run: skips uv install if already on PATH, and skips downloading/extracting
# Antares/Xpansion if a matching-version directory is already present.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 0/5: Prerequisites (curl, tar) ==="
for cmd in curl tar; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ERROR: '$cmd' is required but not found on PATH. Install it and re-run this script." >&2
        exit 1
    fi
done

echo
echo "=== 1/5: uv ==="
if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found on PATH -- installing via the official installer (https://astral.sh/uv/install.sh)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer places uv under ~/.local/bin (or $CARGO_HOME/bin / $XDG_BIN_HOME on some
    # setups) and writes an env file there; source it if present, otherwise just prepend the
    # common location so `uv` is usable for the rest of THIS script.
    for env_file in "$HOME/.local/bin/env" "$HOME/.cargo/bin/env"; do
        # shellcheck disable=SC1090
        [ -f "$env_file" ] && source "$env_file"
    done
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        echo "ERROR: uv installation completed but 'uv' is still not on PATH." >&2
        echo "Open a new shell (or 'source ~/.bashrc') and re-run this script." >&2
        exit 1
    fi
fi
echo "uv ready: $(uv --version)"

echo
echo "=== 2/5: System packages (openmpi, coinor-cbc -- needed by Antares Xpansion/Benders) ==="
_apt_pkgs="openmpi-bin libopenmpi-dev coinor-cbc"
if ! command -v apt-get >/dev/null 2>&1; then
    echo "WARNING: apt-get not found -- install $_apt_pkgs manually for your OS."
elif [ "$(id -u)" = "0" ]; then
    apt-get update && apt-get install -y $_apt_pkgs || echo "WARNING: system package install failed -- see errors above."
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo apt-get update && sudo apt-get install -y $_apt_pkgs || echo "WARNING: system package install failed -- see errors above."
else
    echo "WARNING: not root and passwordless sudo isn't available here -- skipping automatic install."
    echo "Run this manually before using coin/CBC: sudo apt-get install -y $_apt_pkgs"
fi

echo
echo "=== 3/5: Python dependencies (uv sync --group dev, includes xpress) ==="
uv sync --frozen --group dev

echo
echo "=== 4/5: Antares Simulator ==="
ANTARES_VERSION="$(uv run python -c 'from src.dependencies import get_antares_version; print(get_antares_version())')"
ANTARES_DIR="antares-${ANTARES_VERSION}-Ubuntu-22.04"
ANTARES_ARCHIVE="${ANTARES_DIR}.tar.gz"
if [ -d "$ANTARES_DIR" ]; then
    echo "Already present: $ANTARES_DIR"
else
    echo "Downloading Antares Simulator v${ANTARES_VERSION}..."
    curl -L -f -o "$ANTARES_ARCHIVE" \
        "https://github.com/AntaresSimulatorTeam/Antares_Simulator/releases/download/v${ANTARES_VERSION}/${ANTARES_ARCHIVE}"
    tar -xzf "$ANTARES_ARCHIVE"
    rm -f "$ANTARES_ARCHIVE"
    echo "Extracted to $ANTARES_DIR"
fi

echo
echo "=== 5/5: Antares Xpansion ==="
XPANSION_VERSION="$(uv run python -c 'from src.dependencies import get_antares_xpansion_version; print(get_antares_xpansion_version())')"
XPANSION_DIR="antaresXpansion-${XPANSION_VERSION}-ubuntu-22.04"
XPANSION_ARCHIVE="${XPANSION_DIR}.tar.gz"
if [ -d "$XPANSION_DIR" ]; then
    echo "Already present: $XPANSION_DIR"
else
    echo "Downloading Antares Xpansion v${XPANSION_VERSION}..."
    curl -L -f -o "$XPANSION_ARCHIVE" \
        "https://github.com/AntaresSimulatorTeam/antares-xpansion/releases/download/v${XPANSION_VERSION}/${XPANSION_ARCHIVE}"
    tar -xzf "$XPANSION_ARCHIVE"
    rm -f "$XPANSION_ARCHIVE"
    echo "Extracted to $XPANSION_DIR"
fi

echo
echo "=== Done ==="
echo "Antares Simulator : $SCRIPT_DIR/$ANTARES_DIR"
echo "Antares Xpansion  : $SCRIPT_DIR/$XPANSION_DIR"
echo
echo "Run the benchmark, e.g.:"
echo "  uv run python tests/local_benchmark/benchmark_pypsa_vs_xpansion.py \\"
echo "      --input /path/to/network.nc \\"
echo "      --gems-solver-name xpress --xpress-license-path /path/to/xpauth.xpr"
echo
echo "(Use --gems-solver-name coin --pypsa-solver-name cbc instead if you don't have an Xpress license.)"
