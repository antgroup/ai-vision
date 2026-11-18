"""Secret handling for Dataset/Diagnoser spec fields.

Secrets are write-only.
- Create or explicit rotation may submit a secret.
- GET/List never return the secret.
- Omitting a secret during update preserves the existing value.
- An explicit empty-string value clears it.
- Responses expose only a boolean "configured" state for each secret path.

Secret paths are dotted tuples into the spec dict:
  ("model", "api_key")       — Diagnoser LLM model API key
"""

from __future__ import annotations

from typing import Any

SECRET_PATHS: tuple[tuple[str, ...], ...] = (("model", "api_key"),)


def _get_path(d: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _set_path(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cur = d
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def _del_path(d: dict[str, Any], path: tuple[str, ...]) -> None:
    cur: Any = d
    for key in path[:-1]:
        if not isinstance(cur, dict):
            return
        cur = cur.get(key)
    if isinstance(cur, dict):
        cur.pop(path[-1], None)


def redact_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied spec with every secret path replaced by a
    sibling "<field>_configured" boolean. Never emit the secret value."""
    import copy

    out = copy.deepcopy(spec)
    for path in SECRET_PATHS:
        parent: Any = out
        for key in path[:-1]:
            if not isinstance(parent, dict) or key not in parent:
                parent = None
                break
            parent = parent[key]
        if not isinstance(parent, dict):
            continue
        value = _get_path(out, path)
        _del_path(out, path)
        configured_path = path[:-1] + (f"{path[-1]}_configured",)
        _set_path(out, configured_path, bool(value))
    # Specialist private policies need protection in generic GET/List too.
    policy = out.get("specialist_policy")
    if isinstance(policy, dict) and policy.get("kind") == "protected_content_exposure_v1":
        from agentlens.domain.specialist_policy import policy_summary

        out["specialist_policy"] = policy_summary(policy)
    return out


def merge_secret_fields(
    old_spec: dict[str, Any] | None,
    new_spec: dict[str, Any],
) -> dict[str, Any]:
    """Merge secret fields from old_spec into new_spec before persisting.

    - Path absent in new_spec (key not submitted) -> preserve old value.
    - Path present with empty string "" -> explicit clear (stays absent).
    - Path present with non-empty string -> rotate to the new value.
    Non-secret fields in new_spec are left untouched by this function;
    callers are responsible for the rest of the spec merge/replace policy.
    """
    import copy

    out = copy.deepcopy(new_spec)
    old = old_spec or {}
    for path in SECRET_PATHS:
        # Distinguish "omitted" (preserve) from "explicit empty" (clear)
        # by checking presence in the nested new_spec dicts, not just the
        # resolved value (which is None for both omitted and non-dict).
        cur: Any = new_spec
        present = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                present = False
                break
            cur = cur[key]
        if not present:
            old_value = _get_path(old, path)
            if old_value:
                _set_path(out, path, old_value)
            continue
        new_value = _get_path(out, path)
        if new_value == "":
            _del_path(out, path)
    return out
