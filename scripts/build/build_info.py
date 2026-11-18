"""Generate non-secret build identity from the actual checkout and VERSION."""

import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

COMPONENTS = ("aivision-fe", "aivision-be", "antcollector-gw", "agentlens", "aiv")
CHANNELS = ("dev", "daily", "rc", "stable")
VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
)


def git(root, *args):
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def make_info(
    root,
    component,
    *,
    channel="dev",
    source_sha=None,
    build_id=None,
    run_number=None,
    built_at=None,
    product_version=None,
    snapshot_date=None,
    daily_revision="1",
):
    root = Path(root).resolve()
    version = (root / "VERSION").read_text().strip()
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(
            "VERSION must contain a numeric product version, optionally with a prerelease suffix"
        )
    if component not in COMPONENTS or channel not in CHANNELS:
        raise ValueError("unknown component or channel")
    if not isinstance(daily_revision, str) or not re.fullmatch(r"[1-9][0-9]*", daily_revision):
        raise ValueError("daily revision must be a canonical positive integer")
    if snapshot_date not in (None, ""):
        if not isinstance(snapshot_date, str) or not re.fullmatch(r"[0-9]{8}", snapshot_date):
            raise ValueError("snapshot date must use YYYYMMDD")
        datetime.strptime(snapshot_date, "%Y%m%d")
    if channel in {"stable", "rc"} and (snapshot_date not in (None, "") or daily_revision != "1"):
        raise ValueError("release and RC identities do not accept Daily selection inputs")
    if channel != "daily" and daily_revision != "1":
        raise ValueError("only Daily identities can select a daily revision")
    if product_version is not None and product_version != version:
        # Product snapshot versions never mutate VERSION or native packages.
        suffix = (r"-daily\.([0-9]{8})(?:\.[1-9][0-9]*)?" if channel == "daily"
                  else r"-dev\.([0-9]{8})\.[1-9][0-9]*(?:\.[1-9][0-9]*)?")
        match = re.fullmatch(re.escape(version) + suffix, product_version) if isinstance(product_version, str) else None
        if (
            "-" in version
            or channel not in {"dev", "daily"}
            or not match
        ):
            raise ValueError(
                "product version override must match its derived candidate channel"
            )
        if (channel == "dev" or run_number is not None) and (
            not isinstance(run_number, str) or not re.fullmatch(r"[1-9][0-9]*", run_number)
        ):
            raise ValueError("development version requires a positive run number")
        if not isinstance(build_id, str) or not re.fullmatch(
            r"[1-9][0-9]*\.[1-9][0-9]*", build_id
        ):
            raise ValueError("development version requires a full run ID and attempt")
        date = match[1]
        datetime.strptime(date, "%Y%m%d")
        if snapshot_date not in (None, "", date):
            raise ValueError("candidate version differs from its selected snapshot date")
        if channel == "daily":
            expected = f"{version}-daily.{date}"
            if daily_revision != "1":
                expected += f".{daily_revision}"
        else:
            attempt = build_id.split(".")[1]
            expected = f"{version}-dev.{date}.{run_number}"
            if attempt != "1":
                expected += f".{attempt}"
        if product_version != expected:
            raise ValueError(
                "candidate version must match the selected snapshot and revision or development run"
            )
        version = product_version
    actual_sha = git(root, "rev-parse", "HEAD")
    if source_sha is not None and source_sha != actual_sha:
        raise ValueError("declared source SHA does not match the actual checkout")
    status = git(root, "status", "--porcelain", "--untracked-files=normal")
    dirty = bool(status) if status is not None else None
    if channel != "dev" and (actual_sha is None or dirty is not False):
        raise ValueError(
            "release, RC and daily builds require a clean identified checkout"
        )
    if build_id is not None and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", build_id
    ):
        raise ValueError("build ID must be a non-secret identifier")
    if built_at is None:
        built_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)",
        built_at,
    ):
        raise ValueError("build time must be an RFC3339 timestamp")
    timestamp = datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    if timestamp.utcoffset() is None:
        raise ValueError("build time must include a timezone")
    return {
        "schema_version": 1,
        "component": component,
        "product_version": version,
        "component_version": version,
        "source_sha": actual_sha,
        "build_id": build_id,
        "built_at": built_at,
        "target_channel": channel,
        "dirty": dirty,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--component", choices=COMPONENTS, required=True)
    parser.add_argument("--channel", choices=CHANNELS, default="dev")
    parser.add_argument("--source-sha")
    parser.add_argument("--build-id")
    parser.add_argument("--run-number")
    parser.add_argument("--built-at")
    parser.add_argument("--product-version")
    parser.add_argument("--snapshot-date")
    parser.add_argument("--daily-revision", default="1")
    parser.add_argument("--format", choices=("json", "base64"), default="json")
    args = parser.parse_args()
    try:
        value = make_info(
            args.root,
            args.component,
            channel=args.channel,
            source_sha=args.source_sha,
            build_id=args.build_id,
            run_number=args.run_number,
            built_at=args.built_at,
            product_version=args.product_version,
            snapshot_date=args.snapshot_date,
            daily_revision=args.daily_revision,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    print(
        base64.b64encode(payload).decode()
        if args.format == "base64"
        else payload.decode()
    )


if __name__ == "__main__":
    main()
