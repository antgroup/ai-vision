from pathlib import Path
import unittest

import yaml

from slash_commands import ApiError, handle_event


def comment_event(body, *, is_pull=False, commenter="reviewer", action="created"):
    issue = {"number": 7, "user": {"id": 2, "login": "author"}}
    if is_pull:
        issue["pull_request"] = {"url": "https://api.github.com/repos/example/project/pulls/7"}
    return {
        "action": action,
        "comment": {"id": 42, "body": body, "user": {"id": 1, "login": commenter, "type": "User"}},
        "issue": issue,
        "repository": {"default_branch": "main"},
    }


class GitHubFixture:
    def __init__(self, permission="write", role="write"):
        self.permission = permission
        self.role = role
        self.permission_user_id = 1
        self.labels = {"bug", "needs / review"}
        self.issue_labels = {"bug"}
        self.assignees = {"author"}
        self.assignable = {"author", "reviewer"}
        self.pr = {"state": "open", "draft": False, "head": {"sha": "a" * 40}}
        self.issue_status = {"state": "open"}
        self.branch_tips = {"main": "a" * 40, "develop": "b" * 40}
        self.reachable_from = set()
        self.refs = {}
        self.concurrent_tag_target = None
        self.reactions = []
        self.comments = []
        self.reviews = []

    def __call__(self, method, path, data=None):
        if path.startswith("/collaborators/") and path.endswith("/permission") and method == "GET":
            if self.permission is None:
                raise ApiError(404)
            user_id = 2 if path == "/collaborators/author/permission" else self.permission_user_id
            return {"permission": self.permission, "role_name": self.role, "user": {"id": user_id}}
        if path == "/pulls/7" and method == "GET":
            return self.pr
        if path == "/pulls/7/reviews" and method == "POST":
            self.reviews.append(data)
            return data
        if path == "/issues/7" and method == "PATCH":
            if data["state"] != self.issue_status["state"]:
                self.issue_status.update(data)
            return self.issue_status
        if path.startswith("/issues/7/assignees/") and method == "GET":
            if path.rsplit("/", 1)[1] not in self.assignable:
                raise ApiError(404)
            return True
        if path == "/issues/7/assignees":
            if method == "POST":
                self.assignees.update(data["assignees"])
            elif method == "DELETE":
                self.assignees.difference_update(data["assignees"])
            else:
                raise AssertionError((method, path, data))
            return {"assignees": [{"login": login} for login in self.assignees]}
        if path.startswith("/labels/") and method == "GET":
            name = {"needs%20%2F%20review": "needs / review"}.get(path[8:], path[8:])
            if name not in self.labels:
                raise ApiError(404)
            return {"name": name}
        if path == "/issues/7/labels" and method == "POST":
            self.issue_labels.update(data["labels"])
            return [{"name": name} for name in self.issue_labels]
        if path.startswith("/issues/7/labels/") and method == "DELETE":
            name = {"needs%20%2F%20review": "needs / review"}.get(path[17:], path[17:])
            if name not in self.issue_labels:
                raise ApiError(404)
            self.issue_labels.remove(name)
            return True
        if path == "/issues/comments/42/reactions" and method == "POST":
            self.reactions.append(data["content"])
            return {"content": data["content"]}
        if path == "/issues/7/comments" and method == "POST":
            self.comments.append(data["body"])
            return {"body": data["body"]}
        if path.startswith("/git/ref/heads/") and method == "GET":
            branch = path.removeprefix("/git/ref/heads/")
            if branch not in self.branch_tips:
                raise ApiError(404)
            return {"object": {"type": "commit", "sha": self.branch_tips[branch]}}
        if path.startswith("/compare/") and method == "GET":
            target = "c" * 40
            for branch, tip in self.branch_tips.items():
                if path == f"/compare/{target}...{tip}?per_page=1":
                    if branch in self.reachable_from:
                        return {"status": "ahead", "merge_base_commit": {"sha": target}}
                    return {"status": "diverged", "merge_base_commit": {"sha": "d" * 40}}
        if path.startswith("/git/ref/tags/") and method == "GET":
            tag = path.removeprefix("/git/ref/tags/")
            if tag not in self.refs:
                raise ApiError(404)
            return {"object": self.refs[tag]}
        if path == "/git/refs" and method == "POST":
            tag = data["ref"].removeprefix("refs/tags/")
            if self.concurrent_tag_target:
                self.refs[tag] = {"type": "commit", "sha": self.concurrent_tag_target}
                raise ApiError(422)
            if tag in self.refs:
                raise AssertionError(f"Already exists: {tag}")
            self.refs[tag] = {"type": "commit", "sha": data["sha"]}
            return {"object": self.refs[tag]}
        raise AssertionError((method, path, data))


class SlashCommandTests(unittest.TestCase):
    def test_assignment_adds_self_and_preserves_other_assignees(self):
        github = GitHubFixture()
        handle_event(comment_event("/assign"), github)
        self.assertEqual(github.assignees, {"author", "reviewer"})
        self.assertEqual(github.reactions, ["+1"])

    def test_unassignment_removes_named_user_only(self):
        github = GitHubFixture()
        github.assignees.add("reviewer")
        handle_event(comment_event("/unassign @author", is_pull=True), github)
        self.assertEqual(github.assignees, {"reviewer"})

    def test_ineligible_assignee_gets_feedback(self):
        github = GitHubFixture()
        handle_event(comment_event("/assign @unknown"), github)
        self.assertEqual(github.assignees, {"author"})
        self.assertFalse(github.reactions)
        self.assertIn("assigned", github.comments[0])

    def test_read_only_user_cannot_run_a_command(self):
        for permission in (None, "read"):
            with self.subTest(permission=permission):
                github = GitHubFixture(permission)
                handle_event(comment_event("/unassign @author"), github)
                self.assertEqual(github.assignees, {"author"})
                self.assertFalse(github.reactions or github.comments)

    def test_renamed_commenter_cannot_act_with_reassigned_login_permissions(self):
        github = GitHubFixture()
        github.permission_user_id = 57
        handle_event(comment_event("/unassign @author"), github)
        self.assertEqual(github.assignees, {"author"})
        self.assertFalse(github.reactions or github.comments)

    def test_triage_and_moderate_roles_can_manage_issues_without_lgtm_or_tag(self):
        for role in ("triage", "moderate"):
            with self.subTest(role=role):
                github = GitHubFixture("read", role)
                handle_event(comment_event("/assign"), github)
                handle_event(comment_event("/label needs / review", is_pull=True), github)
                handle_event(comment_event("/triage close"), github)
                handle_event(comment_event("/lgtm", is_pull=True), github)
                handle_event(comment_event("/tag workshop " + "a" * 40), github, tag_job=True)
                self.assertIn("reviewer", github.assignees)
                self.assertIn("needs / review", github.issue_labels)
                self.assertEqual(github.issue_status["state"], "closed")
                self.assertFalse(github.reviews or github.refs)
                self.assertEqual(github.reactions, ["+1"] * 3)

    def test_ordinary_comments_and_edits_do_not_call_the_api(self):
        for body, action in (
            ("Please run /assign", "created"),
            ("/assign\n/unassign @author", "created"),
            ("/assign", "edited"),
            ("/help", "created"),
        ):
            with self.subTest(body=body, action=action):
                handle_event(comment_event(body, action=action), None)

    def test_label_and_unlabel_preserve_other_labels_and_encode_the_label_name(self):
        github = GitHubFixture()
        handle_event(comment_event("/label needs / review"), github)
        self.assertEqual(github.issue_labels, {"bug", "needs / review"})
        handle_event(comment_event("/unlabel needs / review"), github)
        self.assertEqual(github.issue_labels, {"bug"})

    def test_label_does_not_create_unknown_labels(self):
        github = GitHubFixture()
        handle_event(comment_event("/label special"), github)
        self.assertEqual(github.issue_labels, {"bug"})
        self.assertEqual(github.labels, {"bug", "needs / review"})
        self.assertIn("does not have", github.comments[0])

    def test_lgtm_requires_open_pr_and_separate_reviewer(self):
        github = GitHubFixture()
        handle_event(comment_event("/lgtm"), github)
        event = comment_event("/lgtm", is_pull=True, commenter="author")
        event["comment"]["user"]["id"] = 2
        handle_event(event, github)
        github.pr["draft"] = True
        handle_event(comment_event("/lgtm", is_pull=True), github)
        self.assertFalse(github.reviews)
        self.assertEqual(len(github.comments), 3)

    def test_lgtm_and_withdrawal_are_advisory_reviews_for_exact_commits(self):
        github = GitHubFixture()
        handle_event(comment_event("/lgtm", is_pull=True), github)
        github.pr["head"]["sha"] = "b" * 40
        handle_event(comment_event("/lgtm cancel", is_pull=True), github)
        self.assertEqual([review["event"] for review in github.reviews], ["COMMENT", "COMMENT"])
        self.assertEqual([review["commit_id"] for review in github.reviews], ["a" * 40, "b" * 40])
        self.assertIn("#issuecomment-42", github.reviews[0]["body"])
        self.assertIn("LGTM withdrawn", github.reviews[1]["body"])

    def test_lgtm_can_reject_a_new_head(self):
        github = GitHubFixture()
        handle_event(comment_event("/lgtm " + "b" * 40, is_pull=True), github)
        self.assertFalse(github.reviews)
        self.assertIn("changed", github.comments[0])

    def test_triage_can_close_and_reopen_an_issue(self):
        github = GitHubFixture()
        for command, expected in (
            ("/triage close", {"state": "closed", "state_reason": "completed"}),
            ("/triage reopen", {"state": "open", "state_reason": "reopened"}),
            ("/triage close not-planned", {"state": "closed", "state_reason": "not_planned"}),
            ("/triage reopen", {"state": "open", "state_reason": "reopened"}),
        ):
            with self.subTest(command=command):
                handle_event(comment_event(command), github)
                self.assertEqual(github.issue_status, expected)

    def test_triage_reports_different_reason_on_already_closed_issue(self):
        github = GitHubFixture()
        handle_event(comment_event("/triage close"), github)
        handle_event(comment_event("/triage close not-planned"), github)
        self.assertEqual(github.issue_status, {"state": "closed", "state_reason": "completed"})
        self.assertEqual(github.reactions, ["+1"])
        self.assertIn("Reopen", github.comments[0])

    def test_triage_does_not_change_pull_request_state(self):
        github = GitHubFixture()
        handle_event(comment_event("/triage close", is_pull=True), github)
        self.assertEqual(github.issue_status, {"state": "open"})
        self.assertIn("On an issue", github.comments[0])

    def test_tag_requires_maintain_or_admin_in_the_separate_job(self):
        command = comment_event("/tag workshop/demo " + "c" * 40, is_pull=True)
        writer = GitHubFixture()
        writer.reachable_from.add("main")
        handle_event(command, writer, tag_job=True)
        self.assertFalse(writer.refs)
        self.assertFalse(writer.reactions)
        maintainer = GitHubFixture(role="maintain")
        maintainer.reachable_from.add("develop")
        handle_event(command, maintainer)
        self.assertFalse(maintainer.refs)
        handle_event(command, maintainer, tag_job=True)
        self.assertEqual(maintainer.refs, {"workshop/demo": {"type": "commit", "sha": "c" * 40}})

    def test_tag_rejects_release_names_invalid_refs_and_unreachable_commits(self):
        for name in ("v1.0.0", "dev", "daily", "latest", "candidate-preview", "demo/../other", "workshop"):
            with self.subTest(name=name):
                github = GitHubFixture("admin", "admin")
                handle_event(comment_event(f"/tag {name} " + "c" * 40), github, tag_job=True)
                self.assertFalse(github.refs)
                self.assertEqual(len(github.comments), 1)

    def test_tag_can_retry_the_same_target_without_moving_existing_refs(self):
        github = GitHubFixture("admin", "admin")
        github.reachable_from.add("main")
        command = comment_event("/tag demo " + "c" * 40)
        github.concurrent_tag_target = "c" * 40
        handle_event(command, github, tag_job=True)
        handle_event(command, github, tag_job=True)
        handle_event(comment_event("/tag demo " + "d" * 40), github, tag_job=True)
        self.assertEqual(github.refs["demo"], {"type": "commit", "sha": "c" * 40})
        self.assertEqual(github.reactions, ["+1", "+1"])
        self.assertIn("already exists", github.comments[0])

    def test_workflow_disables_manual_replays_and_limits_tag_write_permission(self):
        path = Path(__file__).resolve().parents[2] / ".github/workflows/slash-commands.yml"
        workflow = yaml.safe_load(path.read_text())
        self.assertEqual(workflow.get("on", workflow.get(True)), {"issue_comment": {"types": ["created"]}})
        for job in workflow["jobs"].values():
            self.assertIn("github.run_attempt == '1'", job["if"])
            self.assertIn("github.event.comment.author_association", job["if"])
            checkout = job["steps"][0]["with"]
            self.assertEqual(checkout["ref"], "${{ github.sha }}")
            self.assertIs(checkout["persist-credentials"], False)
        self.assertEqual(workflow["jobs"]["conversation"]["permissions"]["contents"], "read")
        self.assertEqual(workflow["jobs"]["tag"]["permissions"]["contents"], "write")


if __name__ == "__main__":
    unittest.main()
