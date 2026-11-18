"""Approved release coordinates for the public AI Vision product."""

import re

PUBLIC_REPOSITORY = "antgroup/ai-vision"
PUBLIC_PACKAGE_ROOT = "ghcr.io/antgroup/ai-vision"
UPSTREAM_SIGNOZ = "ghcr.io/ob-labs/signoz-ob"

IMAGE_ROLES = {
    "aivision-fe": "frontend",
    "aivision-be": "backend",
    "antcollector-gw": "ingest-gateway",
    "agentlens": "diagnosis",
    "signoz-ob": "data-backend",
}


def require_product_repository(repository):
    if repository != PUBLIC_REPOSITORY:
        raise ValueError(f"product releases require the official repository {PUBLIC_REPOSITORY}")
    return repository


def product_image_repositories(repository=PUBLIC_REPOSITORY):
    """Map stable manifest component IDs to approved OCI repositories."""
    require_product_repository(repository)
    return {component: f"{PUBLIC_PACKAGE_ROOT}/{role}"
            for component, role in IMAGE_ROLES.items()}


def validate_data_backend_mirror(record, deployment_ref):
    """Bind the consumed product image to the unchanged upstream index."""
    if not isinstance(record, dict) or set(record) != {"upstream_ref", "mirror_ref"}:
        raise ValueError("public release requires exact data-backend mirror provenance")
    mirror_repo = f"{PUBLIC_PACKAGE_ROOT}/{IMAGE_ROLES['signoz-ob']}"
    for field, repository in (("upstream_ref", UPSTREAM_SIGNOZ), ("mirror_ref", mirror_repo)):
        ref = record[field]
        if not isinstance(ref, str) or not re.fullmatch(
                re.escape(repository) + r"@sha256:[0-9a-f]{64}", ref):
            raise ValueError("data-backend provenance must identify pinned upstream and product indexes")
    if record["mirror_ref"] != deployment_ref:
        raise ValueError("data-backend mirror does not match the deployment ref")
    if record["upstream_ref"].split("@", 1)[1] != record["mirror_ref"].split("@", 1)[1]:
        raise ValueError("data-backend mirror changed the upstream index digest")
    return record
