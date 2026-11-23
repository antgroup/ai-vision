"""Release planning and validation. This module never publishes artifacts."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
)
DIGEST_REF = re.compile(r"[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}")
IMAGES = ("aivision-be", "aivision-fe", "antcollector-gw", "agentlens")


def formal_release_channel(version):
    """Classify an exact release version without changing supported prereleases."""
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("invalid formal release version")
    return "rc" if "-" in version else "stable"


def make_plan(
    version, event, ref, default_branch, tag, run_id, run_number, attempt, source_sha, date,
    snapshot_date=None, daily_revision="1", candidate_channel="auto", personal_edition=False,
    release_preview=False,
):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("invalid root VERSION")
    if type(personal_edition) is not bool or type(release_preview) is not bool:
        raise ValueError("Personal Edition and release preview inputs must be boolean")
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("source must be a full commit SHA")
    if not all(
        isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value)
        for value in (run_id, run_number, attempt)
    ):
        raise ValueError("run ID, run number and attempt must be positive integers")
    if not isinstance(date, str) or not re.fullmatch(r"[0-9]{8}", date):
        raise ValueError("date must use YYYYMMDD")
    datetime.strptime(date, "%Y%m%d")
    if not isinstance(daily_revision, str) or not re.fullmatch(r"[1-9][0-9]*", daily_revision):
        raise ValueError("daily revision must be a canonical positive integer")
    if not isinstance(candidate_channel, str) or candidate_channel not in {"auto", "daily", "dev"}:
        raise ValueError("candidate channel must be auto, daily or dev")
    if snapshot_date not in (None, ""):
        if event != "workflow_dispatch":
            raise ValueError("only a manual candidate can select its snapshot date")
        if not isinstance(snapshot_date, str) or not re.fullmatch(r"[0-9]{8}", snapshot_date):
            raise ValueError("snapshot date must use YYYYMMDD")
        datetime.strptime(snapshot_date, "%Y%m%d")
    selected_date = snapshot_date or date
    if event == "schedule" and (daily_revision != "1" or candidate_channel != "auto"):
        raise ValueError("scheduled Daily builds do not accept manual selection inputs")
    branches = "refs/heads/"
    if release_preview and (event != "workflow_dispatch" or not ref.startswith(branches) or ref == branches):
        raise ValueError("release preview requires a manually selected branch")
    tagged = ref.startswith("refs/tags/v")
    if tagged or release_preview:
        if snapshot_date not in (None, "") or daily_revision != "1" or candidate_channel != "auto":
            raise ValueError("formal releases and previews do not accept Daily selection inputs")
        if tagged and (event not in {"push", "workflow_dispatch"} or ref != f"refs/tags/v{version}"):
            raise ValueError("release tag must exactly match root VERSION")
        channel = formal_release_channel(version)
        product_version, alias, tier = version, version, "release"
        selected_date = ""
        personal_edition = True
    else:
        if event not in {"schedule", "workflow_dispatch"}:
            raise ValueError("unsupported release event")
        alias = "daily" if event == "schedule" else tag
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", alias):
            raise ValueError("invalid candidate tag")
        if (
            alias == "latest"
            or re.match(r"^v?[0-9]+(?:\.|$)", alias)
            or alias.startswith("candidate-")
        ):
            raise ValueError("stable and generated candidate tags are reserved")
        if alias in {"dev", "daily"} and ref != f"refs/heads/{default_branch}":
            raise ValueError("rolling builds must use the current default branch")
        if "-" in version:
            raise ValueError("development builds require a numeric base VERSION")
        channel = ("daily" if alias == "daily" else "dev") if candidate_channel == "auto" else candidate_channel
        if alias in {"daily", "dev"} and channel != alias:
            raise ValueError("rolling alias and candidate channel must agree")
        if channel == "daily":
            product_version = f"{version}-daily.{selected_date}"
            if daily_revision != "1":
                product_version += f".{daily_revision}"
        else:
            if daily_revision != "1":
                raise ValueError("only Daily candidates can select a daily revision")
            product_version = f"{version}-dev.{selected_date}.{run_number}"
            if attempt != "1":
                product_version += f".{attempt}"
        tier = "daily"
    if personal_edition and channel == "dev":
        raise ValueError("Personal Edition requires a Daily, Stable or RC candidate")
    return {
        **({"personal_edition": True} if personal_edition else {}),
        **({"release_preview": True} if release_preview else {}),
        "source_sha": source_sha,
        "product_version": product_version,
        "channel": channel,
        "alias": alias,
        "candidate_tag": f"candidate-{run_id}-{attempt}",
        "build_id": f"{run_id}.{attempt}",
        "run_number": run_number,
        "snapshot_date": selected_date,
        "daily_revision": daily_revision,
        "tier": tier,
        "release_tag": f"v{product_version}",
    }


def workflow_date(created_at):
    """Freeze the Shanghai snapshot day to the original run, including reruns."""
    if not isinstance(created_at, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
        r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", created_at
    ):
        raise ValueError("workflow created_at must be an RFC3339 timestamp with a timezone")
    return datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(
        ZoneInfo("Asia/Shanghai")
    ).strftime("%Y%m%d")


def require_digest(ref):
    if not isinstance(ref, str) or not DIGEST_REF.fullmatch(ref):
        raise ValueError("an explicit image@sha256 digest is required")
    return ref


def require_release_environment(data):
    if data.get("can_admins_bypass") is not False:
        raise ValueError(
            "release environment must report can_admins_bypass=false; missing settings fail closed"
        )
    rules = data.get("protection_rules", [])
    if not any(
        rule.get("type") == "required_reviewers"
        and rule.get("prevent_self_review") is True
        and rule.get("reviewers")
        for rule in rules
    ):
        raise ValueError(
            "release environment requires actual reviewers and prevent_self_review=true"
        )


def require_draft_notes(data):
    if data.get("draft") is not True or data.get("immutable") or data.get("assets"):
        raise ValueError(
            "prepare a mutable draft Release with no assets; published artifacts cannot be overwritten"
        )
    body = re.sub(r"<!--[\s\S]*?-->", "", data.get("body") or "")
    sections = re.split(r"^##\s+", body, flags=re.M)
    for title in ("English", "中文"):
        matched = [
            section.partition("\n")[2]
            for section in sections
            if section.partition("\n")[0].strip() == title
        ]
        if (
            len(matched) != 1
            or not re.sub(r"^#{1,6}.*$", "", matched[0], flags=re.M).strip()
        ):
            raise ValueError("draft Release needs completed English and Chinese notes")
    return data["id"]


def require_platforms(manifest):
    platforms = [
        (
            item.get("platform", {}).get("os"),
            item.get("platform", {}).get("architecture"),
        )
        for item in manifest.get("manifests", [])
        if item.get("platform", {}).get("os") != "unknown"
    ]
    if sorted(platforms) != [("linux", "amd64"), ("linux", "arm64")]:
        raise ValueError(
            "image must contain exactly one linux/amd64 and one linux/arm64 manifest"
        )


def native_build_digest(manifest, pushed_digest, arch):
    if not isinstance(pushed_digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", pushed_digest):
        raise ValueError("pushed image digest must be a SHA256 digest")
    if manifest.get("digest") != pushed_digest:
        raise ValueError("pushed image digest differs from the registry descriptor")
    image_types = {"application/vnd.oci.image.manifest.v1+json",
                   "application/vnd.docker.distribution.manifest.v2+json"}
    if manifest.get("mediaType") in image_types:
        # The caller verifies the platform from the inspected image config.
        return pushed_digest
    children = [item for item in manifest.get("manifests", [])
                if item.get("platform", {}).get("os") != "unknown"
                or item.get("platform", {}).get("architecture") != "unknown"]
    if (len(children) != 1 or children[0]["platform"].get("os") != "linux"
            or children[0]["platform"].get("architecture") != arch
            or children[0].get("mediaType") not in image_types
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", children[0].get("digest", ""))):
        raise ValueError(f"pushed image index must contain exactly one linux/{arch} image manifest")
    return children[0]["digest"]


def require_native_digests(manifest, expected):
    require_platforms(manifest)
    actual = {item["platform"]["architecture"]: item.get("digest")
              for item in manifest["manifests"]
              if item.get("platform", {}).get("os") == "linux"}
    if set(expected) != {"amd64", "arm64"} or len(set(expected.values())) != 2 or not all(
        isinstance(value, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", value)
        for value in [*expected.values(), *actual.values()]
    ) or actual != expected:
        raise ValueError("image index children differ from the two verified native images")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("plan", "environment", "notes", "platforms", "digest", "native-digest")
    )
    parser.add_argument("--file", type=Path)
    parser.add_argument("--digests-directory", type=Path)
    parser.add_argument("--digest")
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    args = parser.parse_args()
    try:
        if args.command == "plan":
            plan = make_plan(
                Path("VERSION").read_text().strip(),
                os.environ["GITHUB_EVENT_NAME"],
                os.environ["GITHUB_REF"],
                os.environ["DEFAULT_BRANCH"],
                os.environ.get("INPUT_TAG", "dev"),
                os.environ["GITHUB_RUN_ID"],
                os.environ["GITHUB_RUN_NUMBER"],
                os.environ["GITHUB_RUN_ATTEMPT"],
                os.environ["GITHUB_SHA"],
                workflow_date(os.environ["RELEASE_CREATED_AT"]),
                snapshot_date=os.environ.get("INPUT_SNAPSHOT_DATE", ""),
                daily_revision=os.environ.get("INPUT_DAILY_REVISION") or "1",
                candidate_channel=os.environ.get("INPUT_CANDIDATE_CHANNEL") or "auto",
                personal_edition=os.environ.get("INPUT_PERSONAL_EDITION") == "true",
                release_preview=os.environ.get("INPUT_RELEASE_PREVIEW") == "true",
            )
            args.file.write_text(json.dumps(plan, indent=2) + "\n")
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                for key, value in plan.items():
                    if isinstance(value, bool):
                        value = json.dumps(value)
                    output.write(f"{key}={value}\n")
        elif args.command == "digest":
            print(require_digest(args.file.read_text().strip()))
        else:
            data = json.loads(args.file.read_text())
            if args.command == "environment":
                require_release_environment(data)
            elif args.command == "notes":
                print(require_draft_notes(data))
            elif args.command == "native-digest":
                if args.arch is None:
                    parser.error("native-digest requires --arch")
                print(native_build_digest(data, args.digest, args.arch))
            else:
                if args.digests_directory:
                    expected = {}
                    for path in args.digests_directory.rglob("*"):
                        if path.is_file() and re.fullmatch(r"[a-f0-9]{64}", path.name):
                            arch = path.parent.name.rsplit("-", 1)[-1]
                            if arch not in {"amd64", "arm64"} or arch in expected:
                                raise ValueError("verified digest artifacts must identify each native architecture once")
                            expected[arch] = "sha256:" + path.name
                    require_native_digests(data, expected)
                else:
                    require_platforms(data)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
