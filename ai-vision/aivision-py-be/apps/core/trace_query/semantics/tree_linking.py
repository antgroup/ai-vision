from __future__ import annotations

import json
from collections.abc import Mapping

from .normalizers import coerce_datetime


def _non_empty_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return str(value)


def _metadata_attributes(observation: Mapping[str, object]) -> Mapping[str, object]:
    metadata = observation.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return {}
    if not isinstance(metadata, Mapping):
        return {}

    attributes = metadata.get("attributes")
    if isinstance(attributes, str):
        try:
            attributes = json.loads(attributes)
        except json.JSONDecodeError:
            return {}
    if not isinstance(attributes, Mapping):
        return {}
    return attributes


def _attribute_value(observation: Mapping[str, object], key: str) -> object | None:
    return _metadata_attributes(observation).get(key)


def _first_string(
    observation: Mapping[str, object],
    keys: tuple[str, ...],
) -> str | None:
    for key in keys:
        value = _non_empty_string(observation.get(key))
        if value is not None:
            return value
    return None


def _span_link_role(observation: Mapping[str, object]) -> str | None:
    role = _non_empty_string(
        _attribute_value(observation, "_internal.span_link.role")
    )
    return role.lower() if role else None


def _sofa_span_id(observation: Mapping[str, object]) -> str | None:
    return _non_empty_string(_attribute_value(observation, "_internal.sofa.span_id"))


def _extract_meta_span_id(observation: Mapping[str, object]) -> str | None:
    direct_value = _first_string(observation, ("metaSpanId", "meta_span_id"))
    if direct_value is not None:
        return direct_value

    metadata_value = _non_empty_string(
        _attribute_value(observation, "__meta__.spanId")
    )
    if metadata_value is not None:
        return metadata_value

    if _span_link_role(observation) == "parent":
        return _sofa_span_id(observation)
    return None


def _extract_span_link_parent_span_id(
    observation: Mapping[str, object],
    meta_span_id: str | None,
) -> str | None:
    direct_value = _first_string(
        observation,
        ("spanLinkParentSpanId", "span_link_parent_span_id"),
    )
    if direct_value is not None:
        return direct_value

    if meta_span_id is not None:
        return None

    if _span_link_role(observation) == "child":
        return _sofa_span_id(observation)
    return None


def _observation_id(observation: Mapping[str, object]) -> str | None:
    return _non_empty_string(observation.get("id"))


def _parent_observation_id(observation: Mapping[str, object]) -> str | None:
    return _first_string(
        observation,
        ("parentObservationId", "parent_observation_id"),
    )


def _start_sort_key(
    index_and_observation: tuple[int, Mapping[str, object]],
) -> tuple[float, int]:
    index, observation = index_and_observation
    value = (
        observation.get("startTime")
        or observation.get("start_time")
        or observation.get("start_time_unix_nano")
    )
    dt = coerce_datetime(value)
    if dt is None:
        return (float("inf"), index)
    return (dt.timestamp(), index)


def _find_fallback_parent_observation_id(
    span_id: str | None,
    span_id_to_observation_id: Mapping[str, str],
    *,
    own_observation_id: str,
    include_self: bool = False,
) -> str | None:
    if not span_id:
        return None

    if include_self:
        direct_parent_id = span_id_to_observation_id.get(span_id)
        if direct_parent_id and direct_parent_id != own_observation_id:
            return direct_parent_id

    span_id_segments = span_id.split(".")
    while len(span_id_segments) > 1:
        span_id_segments.pop()
        candidate_span_id = ".".join(span_id_segments)
        candidate_parent_id = span_id_to_observation_id.get(candidate_span_id)
        if candidate_parent_id and candidate_parent_id != own_observation_id:
            return candidate_parent_id

    return None


def normalize_observation_tree_links(
    observations: list[dict[str, object]],
) -> list[object]:
    indexed_observations: list[tuple[int, Mapping[str, object]]] = [
        (index, observation)
        for index, observation in enumerate(observations)
        if isinstance(observation, Mapping)
    ]
    observation_ids = {
        observation_id
        for _, observation in indexed_observations
        if (observation_id := _observation_id(observation)) is not None
    }

    meta_span_ids: dict[str, str | None] = {}
    span_link_parent_span_ids: dict[str, str | None] = {}
    span_id_to_observation_id: dict[str, str] = {}

    for _, observation in sorted(indexed_observations, key=_start_sort_key):
        observation_id = _observation_id(observation)
        if observation_id is None:
            continue

        meta_span_id = _extract_meta_span_id(observation)
        span_link_parent_span_id = _extract_span_link_parent_span_id(
            observation,
            meta_span_id,
        )
        meta_span_ids[observation_id] = meta_span_id
        span_link_parent_span_ids[observation_id] = span_link_parent_span_id

        if meta_span_id and meta_span_id not in span_id_to_observation_id:
            span_id_to_observation_id[meta_span_id] = observation_id

    normalized: list[object] = []
    for observation in observations:
        if not isinstance(observation, Mapping):
            normalized.append(observation)
            continue

        normalized_observation = dict(observation)
        observation_id = _observation_id(observation)
        if observation_id is None:
            normalized.append(normalized_observation)
            continue

        parent_observation_id = _parent_observation_id(observation)
        has_valid_parent_observation_id = (
            parent_observation_id is not None
            and parent_observation_id != observation_id
            and parent_observation_id in observation_ids
        )
        if has_valid_parent_observation_id:
            normalized_observation["parentObservationId"] = parent_observation_id
            normalized.append(normalized_observation)
            continue

        meta_span_id = meta_span_ids.get(observation_id)
        fallback_parent_observation_id = _find_fallback_parent_observation_id(
            meta_span_id,
            span_id_to_observation_id,
            own_observation_id=observation_id,
        )

        if fallback_parent_observation_id is None and meta_span_id is None:
            fallback_parent_observation_id = _find_fallback_parent_observation_id(
                span_link_parent_span_ids.get(observation_id),
                span_id_to_observation_id,
                own_observation_id=observation_id,
                include_self=True,
            )

        normalized_observation["parentObservationId"] = fallback_parent_observation_id
        normalized.append(normalized_observation)

    return normalized


def normalize_trace_detail_tree_links(
    trace_detail: dict[str, object],
) -> dict[str, object]:
    observations = trace_detail.get("observations")
    if not isinstance(observations, list):
        return trace_detail

    normalized_trace_detail = dict(trace_detail)
    normalized_trace_detail["observations"] = normalize_observation_tree_links(
        observations
    )
    return normalized_trace_detail
