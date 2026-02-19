from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import docker

from .dependencies import load_pypsa_eur_dependencies
from .docker_check import check_pypsa_eur_image

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

IMAGE_TAG_ENV = "PYPSA_EUR_IMAGE_TAG"
WORKSPACE_ENV = "PYPSA_EUR_WORKSPACE"

# Docker Desktop on Linux uses this socket, Python SDK ignores CLI context, so we set it when present.
_DOCKER_DESKTOP_SOCKET = Path.home() / ".docker" / "desktop" / "docker.sock"


def _ensure_docker_host() -> None:
    """Use Docker Desktop socket when present and DOCKER_HOST is not already set."""
    if os.environ.get("DOCKER_HOST"):
        return
    if _DOCKER_DESKTOP_SOCKET.exists():
        os.environ["DOCKER_HOST"] = f"unix://{_DOCKER_DESKTOP_SOCKET}"
        logger.info("Using Docker Desktop socket: %s", _DOCKER_DESKTOP_SOCKET)


def main() -> None:
    _ensure_docker_host()

    # Image: from env or from project-root pypsa_eur_dependencies.json
    image_tag = os.environ.get(IMAGE_TAG_ENV)
    if not image_tag:
        deps = load_pypsa_eur_dependencies()
        image_tag = deps["docker_image"]
    # Workspace: local path on the host (not pulled). Default = current working directory.
    # This path is bind-mounted into the container as /workspace.
    workspace = os.environ.get(WORKSPACE_ENV, str(Path.cwd()))

    try:
        client = docker.from_env()
    except docker.errors.DockerException as e:
        if "Permission denied" in str(e) or "permission" in str(e).lower():
            logger.error(
                "Docker permission denied: this script needs access to the Docker daemon.\n"
                "  • Add your user to the docker group:  sudo usermod -aG docker $USER\n"
                "    Then log out and back in (or reboot). Then run without sudo.\n"
                "  • Or run with sudo using your venv Python (so 'docker' is available):\n"
                "    sudo ./venv/bin/python3 -m pypsa_eur_gen.run_generation"
            )
        else:
            logger.error("Cannot connect to Docker: %s", e)
        sys.exit(1)

    found, tag_to_use = check_pypsa_eur_image(tag=image_tag)
    if not found:
        logger.info("Image %s not found locally; pulling ...", image_tag)
        try:
            client.images.pull(image_tag)
        except docker.errors.ImageNotFound:
            logger.error("Failed to pull image %s (not found in registry).", image_tag)
            sys.exit(1)
        except docker.errors.APIError as e:
            logger.error("Failed to pull image %s: %s", image_tag, e)
            sys.exit(1)
        logger.info("Image %s pulled successfully.", image_tag)
        tag_to_use = image_tag

    workspace_path = Path(workspace).resolve()
    if not workspace_path.is_dir():
        logger.error("Workspace directory does not exist: %s", workspace_path)
        sys.exit(1)

    logger.info(
        "Starting interactive container %s (workspace: %s). Run your commands inside.", tag_to_use, workspace_path
    )
    # Interactive run: current terminal becomes the shell inside the container.
    cmd = [
        "docker",
        "run",
        "-it",
        "--rm",
        "-v",
        f"{workspace_path}:/workspace",
        "-w",
        "/workspace",
        tag_to_use,
        "bash",
    ]
    sys.exit(subprocess.run(cmd).returncode)


if __name__ == "__main__":
    main()
