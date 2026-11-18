"""Shared datasource configuration helpers."""


def resolve_configured_agent_product(config: dict[str, object]) -> str:
    value = config.get("agent_product_filter") or config.get("agent_product")
    if value is None:
        return ""
    return str(value).strip()
