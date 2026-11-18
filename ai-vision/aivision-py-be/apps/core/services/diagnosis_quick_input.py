"""Strict browser grammar and stable intent identity (no database effects)."""

import hashlib
import json
import re
from uuid import UUID


class QuickRunError(Exception):
    def __init__(self, key, status=422, retryable=False):
        self.key, self.status, self.retryable = key, status, retryable
        super().__init__(key)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def normalize_input(body, agent_uuid):
    allowed = {
        "request_id",
        "trace_ids",
        "builtin_keys",
        "scene_key",
        "model_connection_uuid",
        "before_task_uuid",
    }
    if not isinstance(body, dict) or set(body) - allowed:
        raise QuickRunError("QUICK_INPUT_INVALID")
    try:
        request_id = str(UUID(body["request_id"]))
        ids = body["trace_ids"]
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 10
            or any(
                not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", t)
                for t in ids
            )
            or len(set(ids)) != len(ids)
        ):
            raise ValueError()
        result = {
            "request_id": request_id,
            "agent_uuid": str(agent_uuid),
            "trace_ids": sorted(ids),
        }
        if "before_task_uuid" in body:
            before = body["before_task_uuid"]
            if (
                not isinstance(before, str)
                or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", before)
                or set(body) & {"scene_key", "builtin_keys", "model_connection_uuid"}
            ):
                raise ValueError()
            result["before_task_uuid"] = before
        else:
            keys = body["builtin_keys"]
            if (
                not isinstance(keys, list)
                or not 1 <= len(keys) <= 9
                or any(
                    not isinstance(k, str) or not re.fullmatch(r"[a-z_]{1,64}", k)
                    for k in keys
                )
                or len(set(keys)) != len(keys)
            ):
                raise ValueError()
            scene = body.get("scene_key", "quick_runtime")
            if scene not in {"quick_runtime", "coding_result", "conversation_qa"}:
                raise ValueError()
            result.update(builtin_keys=sorted(keys), scene_key=scene)
            if "model_connection_uuid" in body:
                model = body["model_connection_uuid"]
                if not isinstance(model, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,64}", model
                ):
                    raise ValueError()
                result["model_connection_uuid"] = model
        return result
    except (KeyError, ValueError, TypeError, AttributeError):
        raise QuickRunError("QUICK_INPUT_INVALID") from None


def normalize_run_input(body, agent_uuid):
    if not isinstance(body, dict) or set(body) != {
        "request_id",
        "trace_ids",
        "diagnoser_uuid",
    }:
        raise QuickRunError("QUICK_INPUT_INVALID")
    try:
        request_id = str(UUID(body["request_id"]))
        ids = body["trace_ids"]
        diagnoser_uuid = body["diagnoser_uuid"]
        if (
            not isinstance(diagnoser_uuid, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", diagnoser_uuid)
            or not isinstance(ids, list)
            or not 1 <= len(ids) <= 10
            or any(
                not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", t)
                for t in ids
            )
            or len(set(ids)) != len(ids)
        ):
            raise ValueError()
        return {
            "request_id": request_id,
            "trace_ids": sorted(ids),
            "diagnoser_uuid": diagnoser_uuid,
            "agent_uuid": str(agent_uuid),
        }
    except (ValueError, TypeError, AttributeError):
        raise QuickRunError("QUICK_INPUT_INVALID") from None
