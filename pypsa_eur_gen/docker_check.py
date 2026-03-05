from __future__ import annotations

import logging

import docker

logger = logging.getLogger(__name__)


def check_pypsa_eur_image(tag: str = "pypsa-eur:latest") -> tuple[bool, str]:
    """
    Check that the given image (by exact tag or by repo) exists in the local Docker store.

    Tries exact match first (e.g. ghcr.io/pypsa/eur-dev-env:d6383ebf...). If not found,
    lists images for the same repo and returns the first matching image's tag so a
    locally present image with a slightly different tag (e.g. truncated) is still used.

    Args:
        tag: Full image reference (e.g. "ghcr.io/pypsa/eur-dev-env:d6383ebf...").

    Returns:
        (True, tag_to_use) if the image exists (tag_to_use is the reference to run);
        (False, "") if not found.
    """
    client = docker.from_env()
    try:
        image = client.images.get(tag)
        logger.info("Image %s found (ID: %s).", tag, image.id)
        return True, tag
    except docker.errors.ImageNotFound:
        pass

    # Fallback: find by repo name (image name without tag)
    if ":" in tag:
        repo = tag.rsplit(":", 1)[0]
    else:
        repo = tag
    try:
        images = client.images.list(name=repo)
        if images:
            image = images[0]
            use_tag = image.tags[0] if image.tags else f"{repo}:{image.short_id}"
            logger.info(
                "Image not found with exact tag %r; using local image %s (ID: %s).",
                tag,
                use_tag,
                image.id,
            )
            return True, use_tag
    except Exception:  # noqa: BLE001
        pass

    logger.warning("Image %s not found locally.", tag)
    return False, ""
