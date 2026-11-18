USER_SHARED_SPACE_UUID = "s-user_shared"
BIZ_SHARED_SPACE_UUID = "s-biz_shared"
SHARED_SPACE_UUID = "s-shard"
SHARED_SPACE_UUIDS = (USER_SHARED_SPACE_UUID, BIZ_SHARED_SPACE_UUID, SHARED_SPACE_UUID)
DEFAULT_AGENT_UUID = "default"


def is_concrete_scope(space_uuid: str, agent_uuid: str) -> bool:
    """True when the scope names one real business space and one real agent."""
    return (
        bool(space_uuid)
        and space_uuid not in SHARED_SPACE_UUIDS
        and bool(agent_uuid)
        and agent_uuid != DEFAULT_AGENT_UUID
    )


def visible_space_uuids(
    requested_space_uuid: str,
    *,
    include_user_shared: bool = False,
    include_biz_shared: bool = False,
    include_shared: bool = False,
) -> tuple[str, ...]:
    """Return the requested space followed by selected shared spaces."""
    selected = (
        (include_user_shared, USER_SHARED_SPACE_UUID),
        (include_biz_shared, BIZ_SHARED_SPACE_UUID),
        (include_shared, SHARED_SPACE_UUID),
    )
    result = [requested_space_uuid]
    for enabled, space_uuid in selected:
        if enabled and space_uuid not in result:
            result.append(space_uuid)
    return tuple(result)


def require_concrete_agent_uuid(agent_uuid: str) -> str:
    if agent_uuid == DEFAULT_AGENT_UUID:
        raise ValueError("agent_uuid must identify a concrete agent")
    return agent_uuid
