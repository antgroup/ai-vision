"""Select native production unit groups and validate reusable-workflow inputs."""

import json
import os


UNIT_GROUPS = (
    "codex", "claudecode", "pi", "opencode", "deepseek", "hermes", "kimi",
    "onboarding", "harness",
)
PLUGIN_ROOT = "ai-vision/agent-plugins/"


def select_unit_groups(paths, *, force_all=False):
    if force_all:
        return list(UNIT_GROUPS)
    selected = set()
    for path in paths:
        if path.endswith((".md", ".rst")):
            continue
        if not path.startswith(PLUGIN_ROOT):
            return list(UNIT_GROUPS)
        relative = path.removeprefix(PLUGIN_ROOT)
        # Peer edition configs participate in Hermes's shared schema contracts.
        if "/configs/" in relative:
            return list(UNIT_GROUPS)
        group = relative.split("/", 1)[0]
        if group not in UNIT_GROUPS[:7] or "/" not in relative:
            return list(UNIT_GROUPS)
        selected.add(group)
    if not selected:
        return list(UNIT_GROUPS)
    selected.update(("onboarding", "harness"))
    return [group for group in UNIT_GROUPS if group in selected]


def resolve_unit_groups(encoded="all", *, tier="pr", source=False):
    if tier not in {"pr", "daily", "release"} or type(source) is not bool:
        raise ValueError("native unit scope requires a known tier and boolean source selector")
    try:
        groups = list(UNIT_GROUPS) if encoded == "all" else json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError("native unit scope must be all or a nonempty JSON group list") from error
    if (not isinstance(groups, list) or not groups
            or any(not isinstance(group, str) or group not in UNIT_GROUPS for group in groups)
            or len(groups) != len(set(groups))):
        raise ValueError("native unit scope contains empty, unknown or duplicate groups")
    if source or tier != "pr":
        return list(UNIT_GROUPS)
    return [group for group in UNIT_GROUPS if group in groups]


def main():
    source = os.environ.get("NATIVE_SOURCE", "false")
    if source not in {"true", "false"}:
        raise ValueError("NATIVE_SOURCE must be true or false")
    groups = resolve_unit_groups(os.environ.get("NATIVE_UNIT_SCOPE", "all"),
                                 tier=os.environ.get("NATIVE_TIER", "pr"), source=source == "true")
    flags = {group: group in groups for group in UNIT_GROUPS}
    flags.update(rust=any(group in groups for group in ("codex", "claudecode", "kimi")),
                 node=any(group in groups for group in ("pi", "opencode", "deepseek")),
                 python_deps="harness" in groups or "hermes" in groups)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write("## Native unit scope\n\nSelected groups: " + ", ".join(groups) + ".\n")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.writelines(f"{name}={str(value).lower()}\n" for name, value in flags.items())
    print(json.dumps({"groups": groups, "tools": {key: flags[key] for key in ("rust", "node")}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
