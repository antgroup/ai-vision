"""Select test families from the complete Git diff and enforce their results."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from native_unit_scope import UNIT_GROUPS, select_unit_groups

TIERS = ("pr", "daily", "release")
PROFILES = ("basic", "integration", "full")
CHECKS = (
    "frontend",
    "backend",
    "cli",
    "framework_contracts",
    "framework_database",
    "framework_platform",
    "diagnostics_portable",
    "diagnostics_application",
    "diagnostics_database",
    "diagnostics_platform",
    "native_units",
    "native_source",
    "native_platform",
    "collector",
    "adr",
    "deployment",
)
FAMILIES = {
    "framework": (
        "frontend",
        "backend",
        "cli",
        "framework_contracts",
        "framework_database",
        "framework_platform",
    ),
    "diagnostics": (
        "diagnostics_portable",
        "diagnostics_application",
        "diagnostics_database",
        "diagnostics_platform",
    ),
    "native": ("native_units", "native_source", "native_platform"),
    "collector": ("collector",),
    "adr": ("adr",),
    "deployment": ("deployment",),
}
BASIC_CHECKS = frozenset(
    (
        "frontend", "backend", "cli", "framework_contracts", "framework_database",
        "diagnostics_portable", "diagnostics_application", "diagnostics_database",
        "native_units", "collector", "adr",
    )
)
INTEGRATION_CHECKS = frozenset(
    (
        "framework_platform", "diagnostics_platform", "native_source",
        "native_platform", "deployment",
    )
)
PROFILE_CHECKS = {
    "basic": BASIC_CHECKS,
    "integration": INTEGRATION_CHECKS,
    "full": frozenset(CHECKS),
}


def documentation(path):
    # Requirements, executable examples, and configuration are not prose.
    return (
        path.endswith((".md", ".rst"))
        or path.startswith(".github/ISSUE_TEMPLATE/")
        or (
            path.startswith("docs/")
            and path.endswith((".png", ".jpg", ".jpeg", ".svg", ".webp"))
        )
    )


def make_plan(
    paths, *, tier="pr", profile="basic", base_branch="", head_branch="", fallback=False
):
    if tier not in TIERS:
        raise ValueError("tier must be pr, daily, or release")
    if profile not in PROFILES:
        raise ValueError("profile must be basic, integration, or full")
    if profile != "full" and tier != "pr":
        raise ValueError("basic and integration profiles require tier pr")
    paths = set(paths)
    if profile == "full" and (
        "VERSION" in paths
        or any(
            branch.startswith(("release/", "hotfix/"))
            for branch in (base_branch, head_branch)
        )
    ):
        tier = "release"
    plan = {check: False for check in CHECKS}
    reasons = []

    def enable(*checks):
        for check in checks:
            plan[check] = True

    def all_checks(reason):
        enable(*CHECKS)
        reasons.append(reason)

    if tier != "pr" or fallback:
        all_checks(
            "Full tier"
            if tier != "pr"
            else "Diff unavailable: conservative requested coverage"
        )
    else:
        for path in sorted(paths):
            if path.startswith("agentlens/docs/adr/") or path in (
                "agentlens/scripts/adr.py",
                "agentlens/tests/unit/scripts/test_adr.py",
            ):
                enable("adr")
                continue
            if documentation(path):
                continue
            if path.startswith((".github/workflows/", "scripts/ci/")):
                all_checks("CI configuration changed")
            elif path.startswith("scripts/build/"):
                enable("frontend", "backend", "cli")
            elif path.startswith("ai-vision/vfe/") or path == "ai-vision/.nvmrc":
                enable("frontend")
                if path.startswith("ai-vision/vfe/tests/e2e-platform/") or path == "ai-vision/vfe/playwright.platform.config.ts":
                    enable("framework_platform")
            elif path.startswith("ai-vision/aivision-py-be/"):
                enable(
                    "backend",
                    "framework_database",
                    "framework_contracts",
                    "diagnostics_application",
                )
                if any(
                    part in path
                    for part in (
                        "/migrations/",
                        "/models/",
                        "/models.py",
                        "/authentication",
                        "/auth_",
                        "/views/local_auth",
                        "/ingest",
                        "/views/langfuse_ingest",
                        "/trace_query/",
                        "/views/openapi_trace",
                        "/services/diagnosis",
                        "Dockerfile",
                        "uv.lock",
                        "pyproject.toml",
                    )
                ):
                    enable(
                        "frontend",
                        "framework_platform",
                        "native_source",
                        "native_platform",
                        "diagnostics_portable",
                        "diagnostics_database",
                        "diagnostics_platform",
                    )
            elif path.startswith("agentlens/"):
                enable(
                    "diagnostics_portable",
                    "diagnostics_application",
                    "diagnostics_database",
                    "adr",
                )
                if any(
                    part in path
                    for part in (
                        "/web/",
                        "/infra/",
                        "/kernel/",
                        "/migrations/",
                        "/obschema/",
                        "/docker/",
                        "uv.lock",
                        "pyproject.toml",
                    )
                ):
                    enable("frontend", "diagnostics_platform")
            elif path.startswith("antcollector/"):
                enable(
                    "collector",
                    "framework_contracts",
                    "native_source",
                    "frontend",
                    "framework_platform",
                )
            elif path.startswith("aivision-cli/"):
                enable("cli")
                if any(
                    part in path
                    for part in (
                        "/deploy/",
                        "/blueprints/",
                        "/scripts/",
                        "/internal/blueprint/",
                        "/internal/runner/",
                        "/internal/config/",
                        "/internal/proc/",
                        "/internal/state/",
                        "/internal/cli/commands.go",
                        "/internal/cli/generate.go",
                        "/internal/cli/kubernetes.go",
                        "go.mod",
                        "go.sum",
                        "Dockerfile",
                    )
                ):
                    enable("deployment", "frontend", "framework_platform")
            elif path.startswith("deploy/"):
                # Root deployment sources feed both the embedded CLI assets
                # and direct Compose/Kubernetes installation materials.
                enable("cli", "deployment", "frontend", "framework_platform")
            elif path.startswith("docs/deployment/"):
                enable("cli", "deployment")
            elif path.startswith("ai-vision/agent-plugins/tests/diagnostics_e2e/"):
                enable(
                    "frontend",
                    "diagnostics_portable",
                    "diagnostics_application",
                    "diagnostics_database",
                    "diagnostics_platform",
                )
            elif path.startswith("ai-vision/agent-plugins/tests/framework_e2e/"):
                enable(
                    "frontend",
                    "backend",
                    "framework_contracts",
                    "framework_database",
                    "framework_platform",
                )
                if path.endswith(
                    ("docker_platform.py", "signoz_precision.py", "docker_database.py")
                ):
                    enable(
                        "native_source",
                        "native_platform",
                        "diagnostics_portable",
                        "diagnostics_database",
                        "diagnostics_platform",
                    )
            elif path.startswith("ai-vision/agent-plugins/tests/native_agent_e2e/"):
                enable("native_source", "native_platform", "diagnostics_portable")
            elif path.startswith(
                (
                    "ai-vision/agent-plugins/langfuse/",
                    "ai-vision/agent-plugins/nvidia-nemo-relay/",
                )
            ):
                enable(
                    "frontend", "backend", "framework_contracts", "framework_platform"
                )
            elif path.startswith("ai-vision/agent-plugins/"):
                enable("native_source", "native_platform")
                if path.startswith(
                    (
                        "ai-vision/agent-plugins/codex/",
                        "ai-vision/agent-plugins/agentic-otel-common/",
                        "ai-vision/agent-plugins/deepseek/",
                    )
                ):
                    enable("frontend", "diagnostics_portable", "diagnostics_platform")
            elif path in (
                ".gitignore",
                ".gitattributes",
                ".github/release.yml",
                "LICENSE",
                "NOTICE",
                ".github/CODEOWNERS",
            ) or path.startswith("website/"):
                # The website has its own deployment workflow; these paths do
                # not change the platform. Metadata checks still run.
                continue
            else:
                all_checks("Unclassified runtime path")
    if plan["native_source"] or plan["native_platform"]:
        enable("native_units")
    permitted = PROFILE_CHECKS[profile]
    deferred = [check for check in CHECKS if plan[check] and check not in permitted]
    for check in CHECKS:
        plan[check] = plan[check] and check in permitted
    for family, checks in FAMILIES.items():
        plan[family] = any(plan[check] for check in checks)
    plan["native_unit_groups"] = select_unit_groups(
        paths, force_all=fallback or profile != "basic" or tier != "pr"
    ) if plan["native_units"] or plan["native_source"] else []
    plan.update(
        tier=tier, profile=profile, deferred_checks=deferred,
        reasons=sorted(set(reasons)), changed_files=len(paths)
    )
    return plan


def changed_paths(event, event_name, *, root=Path(".")):
    """Use Git, not GitHub's path-filter file limit; retain both sides of moves."""
    if event_name == "pull_request":
        pr = event["pull_request"]
        base, head, separator = pr["base"]["sha"], pr["head"]["sha"], "..."
    elif event_name == "push":
        base, head, separator = event.get("before", ""), event.get("after", ""), ".."
    else:
        return [], True
    if not all(
        re.fullmatch(r"[0-9a-f]{40}", value or "") and value != "0" * 40
        for value in (base, head)
    ):
        return [], True
    try:
        output = subprocess.check_output(
            [
                "git",
                "-C",
                str(root),
                "diff",
                "--no-renames",
                "--name-only",
                "-z",
                base + separator + head,
                "--",
            ],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return [], True
    return [
        path.decode("utf-8", errors="surrogateescape")
        for path in output.split(b"\0")
        if path
    ], False


def check_results(plan, needs, *, expected_profiles=None):
    errors = []
    profile = plan.get("profile")
    if expected_profiles is not None:
        if not expected_profiles or any(
            value not in PROFILES for value in expected_profiles
        ):
            errors.append("Invalid expected test profiles")
        elif profile not in expected_profiles:
            errors.append("Test profile is not allowed for this result gate")
    if profile not in PROFILES:
        errors.append("Missing or invalid test profile")
    else:
        permitted = PROFILE_CHECKS[profile]
        if profile != "full" and plan.get("tier") != "pr":
            errors.append("Basic and integration profiles require tier pr")
        if any(plan.get(check) is True and check not in permitted for check in CHECKS):
            errors.append("Selected check outside test profile")
        deferred = plan.get("deferred_checks")
        if (
            not isinstance(deferred, list)
            or any(
                not isinstance(check, str) or check not in CHECKS or check in permitted
                for check in deferred
            )
            or len(deferred) != len(set(deferred))
        ):
            errors.append("Missing or invalid deferred checks")
    groups = plan.get("native_unit_groups")
    active_units = plan.get("native_units") is True or plan.get("native_source") is True
    if (not isinstance(groups, list)
            or any(not isinstance(group, str) or group not in UNIT_GROUPS for group in groups)
            or len(groups) != len(set(groups))
            or bool(groups) != active_units):
        errors.append("Missing or invalid native unit groups")
    elif active_units and (not {"onboarding", "harness"}.issubset(groups)
                           or not any(group in UNIT_GROUPS[:7] for group in groups)):
        errors.append("Native unit groups must retain production and shared support contracts")
    elif active_units and (profile != "basic" or plan.get("tier") != "pr") and groups != list(UNIT_GROUPS):
        errors.append("Full native source selection requires every unit group")
    for family, checks in FAMILIES.items():
        if plan.get(family) != any(plan.get(check) is True for check in checks):
            errors.append(f"Inconsistent test family: {family}")
    for job in ("scope", "metadata", *FAMILIES):
        required = job in ("scope", "metadata") or plan.get(job) is True
        result = needs.get(job, {}).get("result")
        if required and result != "success":
            errors.append(f"Required job {job}: {result or 'missing'}")
    if plan.get("tier") not in TIERS or any(
        type(plan.get(key)) is not bool for key in (*CHECKS, *FAMILIES)
    ):
        errors.append("Missing or invalid test plan")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path)
    parser.add_argument("--tier", choices=TIERS, default="pr")
    parser.add_argument("--profile", choices=PROFILES, default="basic")
    parser.add_argument("--check-results", action="store_true")
    parser.add_argument("--expected-profile", choices=PROFILES, action="append")
    args = parser.parse_args()
    if args.check_results:
        errors = check_results(
            json.loads(os.environ["CI_PLAN"]), json.loads(os.environ["CI_NEEDS"]),
            expected_profiles=args.expected_profile,
        )
        for error in errors:
            print(error)
        return bool(errors)
    if args.expected_profile:
        parser.error("--expected-profile requires --check-results")
    if args.profile != "full" and args.tier != "pr":
        parser.error("basic and integration profiles require tier pr")
    event = json.loads(args.event.read_text())
    paths, fallback = changed_paths(event, os.environ["GITHUB_EVENT_NAME"])
    base = event.get("pull_request", {}).get("base", {}).get("ref", "")
    head = event.get("pull_request", {}).get("head", {}).get("ref", "") or event.get(
        "ref", ""
    ).removeprefix("refs/heads/")
    plan = make_plan(
        paths, tier=args.tier, profile=args.profile,
        base_branch=base, head_branch=head, fallback=fallback
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    plan["tested_sha"] = sha
    encoded = json.dumps(plan, sort_keys=True)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("plan=" + encoded + "\n")
        output.write("tier=" + plan["tier"] + "\n")
        output.write("profile=" + plan["profile"] + "\n")
        output.write("deferred_checks=" + json.dumps(plan["deferred_checks"]) + "\n")
        output.writelines(f"{key}={str(plan[key]).lower()}\n" for key in FAMILIES)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(
            f"## Test plan\n\nProfile: `{plan['profile']}`; tier: `{plan['tier']}`; actual checkout: `{sha}`.\n\n"
        )
        summary.write("PRs use the merge checkout; a head SHA is not substituted.\n\n")
        for key in CHECKS:
            status = (
                "required" if plan[key]
                else "deferred (not run by this profile)"
                if key in plan["deferred_checks"]
                else "not selected"
            )
            summary.write(f"- {key}: {status}\n")
        summary.write("\nDeferred checks: " + (", ".join(plan["deferred_checks"]) or "none") + ".\n")
        summary.writelines(f"\n{reason}.\n" for reason in plan["reasons"])
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
