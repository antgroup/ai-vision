import copy
import unittest

from release_recovery import JOURNAL_STEP, PUBLISH_JOB, QUALIFICATION_JOBS, verify_original_attempt


REPOSITORY = "antgroup/ai-vision"
SOURCE = "a" * 40
REF = "refs/tags/v0.7.0-rc.1"
RUN = "1000"
ATTEMPT = "2"


class OriginalAttemptTests(unittest.TestCase):
    def setUp(self):
        self.run = {
            "id": int(RUN), "run_attempt": int(ATTEMPT), "head_sha": SOURCE,
            "head_branch": REF.removeprefix("refs/tags/"), "event": "push",
            "path": ".github/workflows/release-images.yml", "status": "completed",
            "repository": {"full_name": REPOSITORY}, "head_repository": {"full_name": REPOSITORY},
            "conclusion": "failure",
        }
        self.attempt = copy.deepcopy(self.run)
        job = {"run_id": int(RUN), "run_attempt": int(ATTEMPT), "head_sha": SOURCE, "status": "completed"}
        self.publisher = dict(job, name=PUBLISH_JOB, conclusion="failure",
                              steps=[{"name": JOURNAL_STEP, "conclusion": "success"}])
        self.jobs = [dict(job, name=name, conclusion="success") for name in sorted(QUALIFICATION_JOBS)]
        self.jobs.append(self.publisher)

    def request(self, path):
        root = f"repos/{REPOSITORY}/actions/runs/{RUN}"
        if path == root:
            return self.run
        if path == f"{root}/attempts/{ATTEMPT}":
            return self.attempt
        prefix = f"{root}/attempts/{ATTEMPT}/jobs?per_page=100&page="
        self.assertTrue(path.startswith(prefix), path)
        start = (int(path.removeprefix(prefix)) - 1) * 100
        return {"total_count": len(self.jobs), "jobs": self.jobs[start:start + 100]}

    def verify(self, *, ref=REF, run=RUN, attempt=ATTEMPT):
        return verify_original_attempt(REPOSITORY, SOURCE, ref, run, attempt, api_fn=self.request)

    def test_failed_cancelled_and_timed_out_publisher_with_qualified_inputs(self):
        for conclusion in ("failure", "cancelled", "timed_out"):
            with self.subTest(conclusion=conclusion):
                self.run["conclusion"] = self.attempt["conclusion"] = conclusion
                self.publisher["conclusion"] = conclusion
                self.assertEqual(self.verify(), REF.removeprefix("refs/tags/"))

    def test_incomplete_inputs_and_different_refs_cannot_request_artifacts(self):
        for values in ({"run": ""}, {"attempt": "02"}, {"ref": "refs/heads/develop"},
                       {"ref": "refs/tags/v0.7.0"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.verify(**values)

    def test_superseded_attempt_cannot_be_recovered(self):
        self.run["run_attempt"] += 1
        with self.assertRaisesRegex(ValueError, "exact interrupted official release attempt"):
            self.verify()

    def test_failed_qualification_and_missing_journal_block_recovery(self):
        qualification = self.jobs[0]
        qualification["conclusion"] = "failure"
        with self.assertRaisesRegex(ValueError, "interruption limited to publication"):
            self.verify()
        qualification["conclusion"] = "skipped"
        with self.assertRaisesRegex(ValueError, "requires the original successful job"):
            self.verify()
        qualification["conclusion"] = "success"
        self.publisher["steps"][0]["conclusion"] = "cancelled"
        with self.assertRaisesRegex(ValueError, "requires the persisted journal"):
            self.verify()

    def test_job_provenance_is_checked_beyond_the_first_page(self):
        self.jobs[:0] = [dict(self.jobs[0], name=f"source job {index}") for index in range(100)]
        self.assertEqual(self.verify(), REF.removeprefix("refs/tags/"))
        self.publisher["head_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "does not belong to the selected completed attempt"):
            self.verify()
