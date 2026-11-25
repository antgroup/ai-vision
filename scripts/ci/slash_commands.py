#!/usr/bin/env python3
"""Apply GitHub slash commands from a trusted workflow's event payload."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ApiError(Exception):
    def __init__(self, status):
        super().__init__(f"GitHub API returned HTTP {status}.")
        self.status = status


class CommandError(Exception):
    """An actionable command error that is safe to post on GitHub."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_request(api_url, repo, token):
    opener = build_opener(NoRedirect())

    def request(method, path, data=None):
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(data).encode("utf-8")
        url = f"{api_url}/repos/{repo}{path}"
        try:
            with opener.open(Request(url, data=payload, headers=headers, method=method), timeout=15) as response:
                return True if response.status == 204 else json.load(response)
        except HTTPError as error:
            raise ApiError(error.code) from None

    return request


def optional_request(request, method, path):
    try:
        return request(method, path)
    except ApiError as error:
        if error.status != 404:
            raise
        return None


def change_assignee(request, issue_path, command, argument, username):
    assignee = (argument or username).removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,79}", assignee):
        raise CommandError(f"Use `/{command}` or `/{command} @username`.")
    path = f"{issue_path}/assignees"
    if command == "assign" and not optional_request(request, "GET", f"{path}/{assignee}"):
        raise CommandError("This user cannot be assigned to this issue or pull request.")
    method = "POST" if command == "assign" else "DELETE"
    result = request(method, path, {"assignees": [assignee]})
    assigned = any(user["login"].casefold() == assignee.casefold() for user in result["assignees"])
    if command == "assign" and not assigned:
        raise CommandError("GitHub did not add the assignee. Check the assignment limit and user eligibility.")


def change_label(request, issue_path, command, name):
    if not name or name in {".", ".."}:
        raise CommandError(f"Use `/{command} <existing label>`.")
    label = optional_request(request, "GET", f"/labels/{quote(name, safe='')}")
    if label is None:
        raise CommandError("This repository does not have that label.")
    path = f"{issue_path}/labels"
    if command == "label":
        request("POST", path, {"labels": [label["name"]]})
    else:
        optional_request(request, "DELETE", f"{path}/{quote(label['name'], safe='')}")


def record_lgtm(request, issue, comment, argument):
    cancel = argument == "cancel"
    if argument and not cancel and not re.fullmatch(r"[0-9a-fA-F]{40}", argument):
        raise CommandError("Use `/lgtm`, `/lgtm <full-commit-sha>`, or `/lgtm cancel`.")
    if issue.get("pull_request") is None:
        raise CommandError("Use `/lgtm` on an open pull request.")
    user = comment["user"]
    if not cancel and user["id"] == issue["user"]["id"]:
        raise CommandError("A pull request author cannot mark their own pull request LGTM.")
    path = f"/pulls/{issue['number']}"
    pr = request("GET", path)
    if pr["state"] != "open" or pr["draft"]:
        raise CommandError("LGTM requires an open pull request that is ready for review.")
    sha = pr["head"]["sha"]
    if argument and not cancel and argument.lower() != sha:
        raise CommandError("The pull request head has changed. Review the current commit before running LGTM.")
    action = "LGTM withdrawn" if cancel else "LGTM"
    command = "/lgtm cancel" if cancel else "/lgtm"
    body = (
        f"{action} for commit `{sha}` by `{user['login']}`. "
        f"[Source command](#issuecomment-{comment['id']}) (`{command}`)."
    )
    request("POST", f"{path}/reviews", {"event": "COMMENT", "commit_id": sha, "body": body})


def create_tag(request, argument, default_branch):
    parts = argument.split()
    if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{40}", parts[1]):
        raise CommandError("Use `/tag <tag-name> <full-commit-sha>`.")
    name, sha = parts[0], parts[1].lower()
    lower_name = name.casefold()
    if lower_name.startswith(("v", "candidate-")) or lower_name in {"dev", "daily", "latest"}:
        raise CommandError("That tag name is reserved for the release workflows.")
    ref = f"refs/tags/{name}"
    if not name.isprintable() or len(name) > 255 or subprocess.run(
        ["git", "check-ref-format", ref], capture_output=True
    ).returncode:
        raise CommandError("Use a valid Git tag name.")

    encoded_name = quote(name, safe="/")
    endpoint = f"/git/ref/tags/{encoded_name}"
    existing_ref = optional_request(request, "GET", endpoint)
    if existing_ref is not None:
        target = existing_ref["object"]
        if target["type"] == "commit" and target["sha"] == sha:
            return
        raise CommandError("That tag already exists. Choose a different name.")

    for branch in dict.fromkeys((default_branch, "develop")):
        branch_ref = optional_request(request, "GET", f"/git/ref/heads/{quote(branch, safe='/')}")
        if branch_ref is None:
            continue
        tip = branch_ref["object"]["sha"]
        if tip == sha:
            break
        comparison = optional_request(request, "GET", f"/compare/{sha}...{tip}?per_page=1")
        if comparison and comparison["status"] == "ahead" and comparison["merge_base_commit"]["sha"] == sha:
            break
    else:
        raise CommandError("Use a commit reachable from the default branch or `develop`.")

    try:
        request("POST", "/git/refs", {"ref": ref, "sha": sha})
    except ApiError as error:
        if error.status not in {409, 422}:
            raise
        # Another run can create the same tag between our GET and POST.
        existing_ref = optional_request(request, "GET", endpoint)
        if existing_ref is None:
            raise
        target = existing_ref["object"]
        if target["type"] != "commit" or target["sha"] != sha:
            raise CommandError("That tag already exists. Choose a different name.") from None


def handle_event(event, request, *, tag_job=False):
    if event.get("action") != "created":
        return
    comment, issue = event["comment"], event["issue"]
    if comment["user"]["type"] != "User":
        return
    match = re.fullmatch(r"/([a-z]+)(?: +([^\r\n]+))?", comment["body"].rstrip(), re.IGNORECASE | re.ASCII)
    if match is None:
        return
    command, argument = match[1].lower(), (match[2] or "").strip()
    if tag_job:
        if command != "tag":
            return
    elif command not in {"assign", "unassign", "label", "unlabel", "lgtm", "triage"}:
        return

    user = comment["user"]
    username = user["login"]
    permission = optional_request(request, "GET", f"/collaborators/{quote(username, safe='')}/permission")
    if permission is None or (permission.get("user") or {}).get("id") != user["id"]:
        return
    role = permission.get("role_name")
    can_write = permission["permission"] in {"admin", "write"}
    if tag_job:
        allowed = can_write and role in {"admin", "maintain"}
    else:
        allowed = can_write or (command != "lgtm" and role in {"triage", "moderate"})
    if not allowed:
        return

    issue_path = f"/issues/{issue['number']}"
    try:
        if tag_job:
            create_tag(request, argument, event["repository"]["default_branch"])
        elif command in {"assign", "unassign"}:
            change_assignee(request, issue_path, command, argument, username)
        elif command in {"label", "unlabel"}:
            change_label(request, issue_path, command, argument)
        elif command == "lgtm":
            record_lgtm(request, issue, comment, argument)
        else:
            states = {
                "close": {"state": "closed", "state_reason": "completed"},
                "close not-planned": {"state": "closed", "state_reason": "not_planned"},
                "reopen": {"state": "open", "state_reason": "reopened"},
            }
            if argument not in states or issue.get("pull_request") is not None:
                raise CommandError("On an issue, use `/triage close`, `/triage close not-planned`, or `/triage reopen`.")
            update = states[argument]
            result = request("PATCH", issue_path, update)
            if update["state"] == "closed" and result.get("state_reason") != update["state_reason"]:
                raise CommandError("Reopen the issue before closing it with a different reason.")
    except CommandError as error:
        request("POST", f"{issue_path}/comments", {"body": str(error)})
        return
    request("POST", f"/issues/comments/{comment['id']}/reactions", {"content": "+1"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--tag-job", action="store_true")
    args = parser.parse_args()
    event = json.loads(args.event.read_text(encoding="utf-8"))
    request = github_request(os.environ["GITHUB_API_URL"], os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    handle_event(event, request, tag_job=args.tag_job)


if __name__ == "__main__":
    main()
