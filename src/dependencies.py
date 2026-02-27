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

"""Load and expose Antares / Antares Xpansion versions from dependencies.json."""

import json
from pathlib import Path
from typing import Any, cast


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_dependencies() -> dict[str, Any]:
    path = _repo_root() / "dependencies.json"
    if not path.exists():
        return {}
    with path.open() as f:
        return cast(dict[str, Any], json.load(f))


_deps: dict[str, Any] | None = None


def get_dependencies() -> dict[str, Any]:
    """Return the content of dependencies.json (cached)."""
    global _deps
    if _deps is None:
        _deps = _load_dependencies()
    return _deps


def get_antares_version() -> str:
    """Return antares_version from dependencies.json (e.g. '9.3.5')."""
    return str(get_dependencies().get("antares_version", "9.3.5"))


def get_antares_xpansion_version() -> str:
    """Return antares_xpansion_version from dependencies.json (e.g. '1.7.2')."""
    return str(get_dependencies().get("antares_xpansion_version", "1.7.2"))


def get_antares_dir_name() -> str:
    """Return Antares archive/dir name (e.g. 'antares-9.3.5-Ubuntu-22.04')."""
    return f"antares-{get_antares_version()}-Ubuntu-22.04"


def get_antares_xpansion_dir_name() -> str:
    """Return Antares Xpansion archive/dir name (e.g. 'antaresXpansion-1.7.2-ubuntu-22.04')."""
    return f"antaresXpansion-{get_antares_xpansion_version()}-ubuntu-22.04"


def get_antares_modeler_bin(base_dir: Path) -> Path:
    """Return path to antares-modeler binary under base_dir."""
    return base_dir / get_antares_dir_name() / "bin" / "antares-modeler"

def get_antares_problem_generator_bin(base_dir: Path) -> Path:
    """Return path to antares-problem-generator binary under base_dir."""
    return base_dir / get_antares_dir_name() / "bin" / "antares-problem-generator"


def get_antares_xpansion_benders_bin(base_dir: Path) -> Path:
    """Return path to benders binary under base_dir (Antares Xpansion)."""
    return base_dir / get_antares_xpansion_dir_name() / "bin" / "benders"
