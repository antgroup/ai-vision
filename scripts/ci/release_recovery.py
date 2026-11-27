"""Download the frozen inputs of an interrupted, approved formal publication."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from product_registry import require_product_repository
from release_candidate import BACKENDS
from release_publish import api


PUBLISH_JOB = "publish reviewed stable or RC artifacts"
JOURNAL_STEP = "Persist the formal publication journal"
INTERRUPTED = {"failure", "cancelled", "timed_out"}
QUALIFICATION_JOBS = {
    "verify stable publication prerequisites",
    "verify public candidate indexes anonymously",
    "freeze qualified candidate acceptance",
    "freeze qualified Personal Edition acceptance",
    "cli / qualify Windows x64",
    *(f"Linux amd64 candidate deployment ({backend})" for backend in BACKENDS),
}


def verify_original_attempt(repository, source_sha, ref, run_id, attempt, *, api_fn=api):
    require_product_repository(repository)
    if not all(re.fullmatch(r"[1-9][0-9]*", value) for value in (run_id, attempt)):
        raise ValueError("formal recovery requires both the original run ID and attempt")
    if not ref.startswith("refs/tags/v"):
        raise ValueError("dispatch formal recovery from the original version tag")
    tag = ref.removeprefix("refs/tags/")
    run_path = f"repos/{repository}/actions/runs/{run_id}"
    attempt_path = f"{run_path}/attempts/{attempt}"
    for path in (run_path, attempt_path):
        run = api_fn(path)
        if (run.get("id") != int(run_id) or run.get("run_attempt") != int(attempt)
                or run.get("head_sha") != source_sha or run.get("head_branch") != tag
                or run.get("path") != ".github/workflows/release-images.yml"
                or run.get("event") not in {"push", "workflow_dispatch"}
                or run.get("repository", {}).get("full_name") != repository
                or run.get("head_repository", {}).get("full_name") != repository
                or run.get("status") != "completed" or run.get("conclusion") not in INTERRUPTED):
            raise ValueError("select the exact interrupted official release attempt at this tag")

    jobs = []
    page = 1
    while True:
        response = api_fn(f"{attempt_path}/jobs?per_page=100&page={page}")
        jobs.extend(response["jobs"])
        if len(jobs) >= response["total_count"]:
            if len(jobs) != response["total_count"]:
                raise ValueError("formal recovery job listing is inconsistent")
            break
        if not response["jobs"]:
            raise ValueError("formal recovery job listing is incomplete")
        page += 1

    for job in jobs:
        if (job.get("run_id") != int(run_id) or job.get("run_attempt") != int(attempt)
                or job.get("head_sha") != source_sha or job.get("status") != "completed"):
            raise ValueError("formal recovery job does not belong to the selected completed attempt")
        if job.get("name") != PUBLISH_JOB and job.get("conclusion") not in {"success", "skipped"}:
            raise ValueError("formal recovery requires an interruption limited to publication")
    for name in QUALIFICATION_JOBS:
        if [job.get("conclusion") for job in jobs if job.get("name") == name] != ["success"]:
            raise ValueError(f"formal recovery requires the original successful job: {name}")
    publishers = [job for job in jobs if job.get("name") == PUBLISH_JOB]
    if (len(publishers) != 1 or publishers[0].get("conclusion") not in INTERRUPTED
            or [step.get("conclusion") for step in publishers[0].get("steps", [])
                if step.get("name") == JOURNAL_STEP] != ["success"]):
        raise ValueError("formal recovery requires the persisted journal from the interrupted publisher")
    return tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("attempt")
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    source_sha = os.environ["GITHUB_SHA"]
    tag = verify_original_attempt(repository, source_sha, os.environ["GITHUB_REF"], args.run_id, args.attempt)
    for name, directory in (("formal-publication", "formal"), ("release-assets", "release"),
                            ("release-acceptance", "acceptance")):
        subprocess.run(["gh", "run", "download", args.run_id, "--repo", repository,
                        "--name", f"{name}-{args.attempt}", "--dir", directory], check=True)
    journal = json.loads(Path("formal/formal-publication.json").read_text())
    if (journal.get("candidate_build_id") != f"{args.run_id}.{args.attempt}"
            or journal.get("source_sha") != source_sha or journal.get("source_repository") != repository
            or journal.get("release_tag") != tag):
        raise ValueError("formal journal does not belong to the original run and release tag")


if __name__ == "__main__":
    main()
