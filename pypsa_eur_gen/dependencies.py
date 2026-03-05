from __future__ import annotations

import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULTS_PATH = _PROJECT_ROOT / "pypsa_eur_dependencies.json"


def load_pypsa_eur_dependencies() -> dict[str, str]:
    """
    Load PyPSA-Eur dependencies from pypsa_eur_dependencies.json
    """
    if not _DEFAULTS_PATH.is_file():
        raise FileNotFoundError(
            f"Missing {_DEFAULTS_PATH}. Create it at the project root with e.g. "
            '{"docker_image": "ghcr.io/pypsa/eur-dev-env:<tag>"}'
        )
    data = json.loads(_DEFAULTS_PATH.read_text())
    docker_image = data.get("docker_image")
    if not isinstance(docker_image, str) or not docker_image.strip():
        raise ValueError(f'Invalid "docker_image" in {_DEFAULTS_PATH}: expected a non-empty string.')
    return {
        "docker_image": docker_image,
    }
