"""Tests for the labs skill registry import endpoints (skills.sh + GitHub)."""

import io
import json
import tarfile
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.core.models import Space
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_space_member,
)
from apps.core.views import labs_skill_registry as registry
from apps.core.views.labs_skill_registry import (
    _download_tarball,
    frontmatter_summary,
    iter_repo_skills,
    parse_github_target,
)


def _build_tar(files: dict[str, str]) -> bytes:
    """Builds an in-memory tarball whose members sit under the usual
    `<repo>-<ref>/` prefix GitHub uses."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name=f"skills-main/{path}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


SKILL_A = "---\nname: skill-a\ndescription: First skill\n---\n\n# A"
SKILL_B = (
    "---\nname: skill-b\ndescription: >-\n  Folded long\n  description\n---\n\n# B"
)


class ParseGithubTargetTests(SimpleTestCase):
    def test_bare_repo_shapes(self):
        for value in (
            "antfu/skills",
            "https://github.com/antfu/skills",
            "http://github.com/antfu/skills/",
            "https://www.github.com/antfu/skills?tab=readme",
        ):
            target = parse_github_target(value)
            self.assertEqual((target["owner"], target["repo"]), ("antfu", "skills"))
            self.assertEqual(target["kind"], "")

    def test_tree_url_with_subdir(self):
        target = parse_github_target(
            "https://github.com/antfu/skills/tree/main/skills/vite"
        )
        self.assertEqual(target["kind"], "tree")
        self.assertEqual(target["tree_rest"], ["main", "skills", "vite"])

    def test_blob_skill_md_url(self):
        target = parse_github_target(
            "https://github.com/antfu/skills/blob/main/skills/vite/SKILL.md"
        )
        self.assertEqual(target["kind"], "blob")
        self.assertEqual(target["tree_rest"][-1], "SKILL.md")

    def test_rejects_other_shapes(self):
        for value in (
            "https://gitlab.com/antfu/skills",
            "https://github.com/antfu/skills/issues/1",
            "https://github.com/antfu/skills/blob/main/README.md",
            "https://github.com/antfu",
            "",
        ):
            self.assertIsNone(parse_github_target(value), value)


class FrontmatterSummaryTests(SimpleTestCase):
    def test_plain_pair_with_quotes(self):
        name, description = frontmatter_summary(
            '---\nname: "blog-post"\ndescription: write posts\n---\n'
        )
        self.assertEqual(name, "blog-post")
        self.assertEqual(description, "write posts")

    def test_folded_description(self):
        _, description = frontmatter_summary(SKILL_B)
        self.assertEqual(description, "Folded long description")

    def test_literal_description(self):
        _, description = frontmatter_summary(
            "---\nname: x\ndescription: |\n  line one\n  line two\n---\n"
        )
        self.assertEqual(description, "line one\nline two")

    def test_no_frontmatter(self):
        self.assertEqual(frontmatter_summary("# just a doc"), ("", ""))


class IterRepoSkillsTests(SimpleTestCase):
    def setUp(self):
        self.tarball = _build_tar(
            {
                "SKILL.md": SKILL_A,
                "skills/skill-b/SKILL.md": SKILL_B,
                "skills/skill-b/README.md": "# not a skill",
                "skills/MYSKILL.md.txt": "nope",
            }
        )

    def test_lists_every_skill(self):
        skills = iter_repo_skills(self.tarball)
        self.assertEqual([item["path"] for item in skills], ["SKILL.md", "skills/skill-b/SKILL.md"])
        self.assertEqual(skills[0]["name"], "skill-a")
        self.assertEqual(skills[1]["dirname"], "skill-b")
        self.assertEqual(skills[1]["name"], "skill-b")
        self.assertEqual(skills[1]["description"], "Folded long description")

    def test_subdir_filter(self):
        skills = iter_repo_skills(self.tarball, subdir="skills")
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["path"], "skills/skill-b/SKILL.md")

class DownloadTarballCacheTests(SimpleTestCase):
    def setUp(self):
        registry._TARBALL_CACHE.clear()

    def test_second_call_hits_cache(self):
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [b"tar-bytes"]
        with patch.object(registry.requests, "get", return_value=response) as mock_get:
            first = _download_tarball("antfu", "skills", "main")
            second = _download_tarball("antfu", "skills", "main")
        self.assertEqual(first, b"tar-bytes")
        self.assertEqual(second, b"tar-bytes")
        self.assertEqual(mock_get.call_count, 1)

    def test_oversized_archive_rejected(self):
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [b"x" * (registry.MAX_TARBALL_BYTES + 1)]
        with patch.object(registry.requests, "get", return_value=response), self.assertRaises(
            registry.RegistryUpstreamError
        ) as ctx:
            _download_tarball("antfu", "skills", "main")
        self.assertEqual(ctx.exception.code, 3124)


class SkillRegistryApiTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_local_user("registry@example.com")
        self.space = Space.objects.create(
            name="labs-skill-registry", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        registry._TARBALL_CACHE.clear()

    def _search(self, query: str, **params):
        return self.client.get(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/registry/search",
            {"q": query, **params},
            format="json",
        )

    def test_search_requires_two_characters(self):
        response = self._search("s")
        self.assertEqual(response.status_code, 400)

    def test_search_proxies_public_endpoint(self):
        upstream = MagicMock(status_code=200)
        upstream.json.return_value = {
            "searchType": "fuzzy",
            "skills": [
                {
                    "id": "antfu/skills/vite",
                    "source": "antfu/skills",
                    "skillId": "vite",
                    "name": "vite",
                    "installs": 1234,
                }
            ],
        }
        with patch.object(registry.requests, "get", return_value=upstream) as mock_get:
            response = self._search("vite")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["skills"][0]["url"], "https://skills.sh/antfu/skills/vite")
        self.assertEqual(mock_get.call_args.kwargs["params"], {"q": "vite", "limit": 200})

    def test_search_upstream_failure_maps_to_502(self):
        upstream = MagicMock(status_code=500)
        with patch.object(registry.requests, "get", return_value=upstream):
            response = self._search("vite")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], 3121)

    def _patch_repo(self, tarball: bytes):
        return patch.object(registry, "_download_tarball", return_value=tarball)

    def test_resolve_source_lists_skills(self):
        tarball = _build_tar({"skills/skill-b/SKILL.md": SKILL_B})
        with self._patch_repo(tarball):
            response = self.client.post(
                f"/api/v2/spaces/{self.space.uuid}/labs/skill/registry/resolve",
                {"source": "antfu/skills"},
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["source"], "antfu/skills")
        self.assertEqual(data["branch"], "main")
        self.assertEqual(data["skills"][0]["name"], "skill-b")

    def test_resolve_rejects_bad_source(self):
        response = self.client.post(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/registry/resolve",
            {"source": "not-a-source"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_resolve_tree_url_filters_subdir(self):
        tarball = _build_tar(
            {"SKILL.md": SKILL_A, "skills/skill-b/SKILL.md": SKILL_B}
        )
        probe = MagicMock(status_code=200)
        with self._patch_repo(tarball), patch.object(registry.requests, "get", return_value=probe):
            response = self.client.post(
                f"/api/v2/spaces/{self.space.uuid}/labs/skill/registry/resolve",
                {"url": "https://github.com/antfu/skills/tree/main/skills"},
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["branch"], "main")
        self.assertEqual(data["subdir"], "skills")
        self.assertEqual(
            [item["path"] for item in data["skills"]], ["skills/skill-b/SKILL.md"]
        )

    def _resolve(self, **payload):
        return self.client.post(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/registry/resolve",
            payload,
            format="json",
        )

    def _tree(self, *paths, truncated=False):
        return json.dumps({
            "truncated": truncated,
            "tree": [
                {"type": "blob", "mode": "100644", "path": path, "sha": "a" * 40}
                for path in paths
            ],
        }).encode()

    def test_registry_selection_loads_tree_and_one_blob_without_archive(self):
        path = "skills/skill-b/SKILL.md"
        tree = self._tree("assets/large.bin", "skills/skill-a/SKILL.md", path)
        with (
            patch.object(registry, "_download_tarball") as archive,
            patch.object(registry, "_read_remote", side_effect=[tree, SKILL_B.encode()]) as read,
        ):
            response = self._resolve(source="antfu/skills", skillId="skill-b")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["branch"], "HEAD")
        self.assertEqual(len(data["skills"]), 1)
        self.assertEqual(data["skills"][0]["path"], path)
        self.assertEqual(data["skills"][0]["content"], SKILL_B)
        archive.assert_not_called()
        self.assertEqual(read.call_count, 2)
        self.assertTrue(read.call_args_list[0].args[0].endswith("/git/trees/HEAD"))
        self.assertTrue(read.call_args_list[1].args[0].endswith(f"/git/blobs/{'a' * 40}"))

    def test_registry_selection_never_falls_back_to_archive_or_imports_wrong_skill(self):
        path = "skills/skill-b/SKILL.md"
        cases = [
            ("main", [self._tree("skills/skill-a/SKILL.md")]),
            ("", [self._tree(path, "extra/skill-b/SKILL.md")]),
            ("", [self._tree(path, truncated=True)]),
            ("main", [self._tree(path), SKILL_A.encode()]),
        ]
        for ref, upstream in cases:
            with (
                self.subTest(upstream=upstream),
                patch.object(registry, "_download_tarball") as archive,
                patch.object(registry, "_read_remote", side_effect=upstream) as read,
            ):
                response = self._resolve(source="antfu/skills", skillId="skill-b", ref=ref)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], 3123)
                archive.assert_not_called()
                self.assertEqual(read.call_count, len(upstream))

    def test_registry_selection_reads_a_matching_root_skill(self):
        tree = self._tree("SKILL.md", "README.md")
        with (
            patch.object(registry, "_download_tarball") as archive,
            patch.object(registry, "_read_remote", side_effect=[tree, SKILL_B.encode()]),
        ):
            response = self._resolve(source="antfu/skills", skillId="skill-b", ref="v1")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["branch"], "v1")
        self.assertEqual(data["skills"][0]["path"], "SKILL.md")
        self.assertEqual(data["skills"][0]["content"], SKILL_B)
        archive.assert_not_called()

    def test_registry_selection_rejects_a_matching_root_and_nested_skill(self):
        tree = self._tree("SKILL.md", "skills/skill-b/SKILL.md")
        content = SKILL_B.encode()
        with patch.object(registry, "_read_remote", side_effect=[tree, content, content]) as read:
            response = self._resolve(source="antfu/skills", skillId="skill-b")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], 3123)
        self.assertEqual(read.call_count, 3)

    def test_registry_selection_accepts_a_matching_snapshot_for_a_different_directory_name(self):
        tree = self._tree("skills/firebase-data-connect-basics/SKILL.md")
        text = "---\nname: firebase-data-connect\ndescription: Firebase SQL Connect\n---\n"
        snapshot = json.dumps({"files": [{"path": "SKILL.md", "contents": text}]}).encode()
        with (
            patch.object(registry, "_download_tarball") as archive,
            patch.object(registry, "_read_remote", side_effect=[tree, snapshot]) as read,
        ):
            response = self._resolve(source="firebase/agent-skills", skillId="firebase-data-connect")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["branch"], "")
        self.assertEqual(data["skills"][0]["content"], text)
        self.assertTrue(data["skills"][0]["registrySnapshot"])
        self.assertEqual(read.call_args.args[0], "https://skills.sh/api/download/firebase/agent-skills/firebase-data-connect")
        archive.assert_not_called()

    def test_registry_snapshot_fallback_keeps_only_matching_skill_without_a_git_ref(self):
        rate_limit = registry.RegistryUpstreamError("Rate limited", 3122, 429)
        for skill_text, status in ((SKILL_B, 200), (SKILL_A, 429)):
            snapshot = json.dumps({
                "files": [
                    {"path": "references/helper.md", "contents": "# Helper"},
                    {"path": "SKILL.md", "contents": skill_text},
                ],
            }).encode()
            with (
                self.subTest(status=status),
                patch.object(registry, "_download_tarball") as archive,
                patch.object(registry, "_read_remote", side_effect=[rate_limit, snapshot]) as read,
            ):
                response = self._resolve(source="antfu/skills", skillId="skill-b")
                self.assertEqual(response.status_code, status)
                if status == 200:
                    data = response.json()["data"]
                    self.assertEqual(data["branch"], "")
                    self.assertEqual(len(data["skills"]), 1)
                    self.assertEqual(data["skills"][0]["content"], SKILL_B)
                    self.assertTrue(data["skills"][0]["registrySnapshot"])
                self.assertEqual(
                    read.call_args.args[0], "https://skills.sh/api/download/antfu/skills/skill-b"
                )
                archive.assert_not_called()

    def test_registry_snapshot_can_resolve_github_not_found_for_unpinned_selection(self):
        not_found = registry.RegistryUpstreamError("Upstream resource not found", 3123, 404)
        snapshot = json.dumps({"files": [{"path": "SKILL.md", "contents": SKILL_B}]}).encode()
        for ref, status in (("", 200), ("v1", 404)):
            with (
                self.subTest(ref=ref),
                patch.object(registry, "_read_remote", side_effect=[not_found, snapshot]) as read,
            ):
                response = self._resolve(source="antfu/skills", skillId="skill-b", ref=ref)
                self.assertEqual(response.status_code, status)
                if status == 200:
                    self.assertTrue(response.json()["data"]["skills"][0]["registrySnapshot"])
                self.assertEqual(read.call_count, 2 if status == 200 else 1)

    def test_explicit_blob_link_reads_only_requested_file(self):
        for ref, path in (("main", "SKILL.md"), ("a" * 40, "skills/skill-b/SKILL.md")):
            with (
                self.subTest(ref=ref, path=path),
                patch.object(registry, "_download_tarball") as archive,
                patch.object(registry, "_read_remote", return_value=SKILL_B.encode()) as read,
            ):
                response = self._resolve(url=f"https://github.com/antfu/skills/blob/{ref}/{path}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["data"]["skills"][0]["content"], SKILL_B)
                read.assert_called_once_with(
                    f"https://raw.githubusercontent.com/antfu/skills/{ref}/{path}",
                    params={},
                    raw=True,
                )
                archive.assert_not_called()

    def test_blob_link_keeps_the_full_slash_branch_and_repo_relative_path(self):
        path = "skills/skill-b/SKILL.md"
        for ref in ("feature/review", "refs/heads/feature/review"):
            def upstream(url, *, params, raw=False):
                if url.startswith("https://raw.githubusercontent.com/"):
                    return SKILL_B.encode()
                if url.endswith(f"/contents/{path}") and params.get("ref") == ref:
                    return SKILL_B.encode()
                raise registry.RegistryUpstreamError("Not found", 3123, 404)

            with (
                self.subTest(ref=ref),
                patch.object(registry, "_download_tarball") as archive,
                patch.object(registry, "_read_remote", side_effect=upstream),
            ):
                response = self._resolve(url=f"https://github.com/antfu/skills/blob/{ref}/{path}")
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertEqual(data["branch"], ref)
                self.assertEqual(data["skills"][0]["path"], path)
                self.assertEqual(data["skills"][0]["content"], SKILL_B)
                archive.assert_not_called()

    def test_blob_link_rejects_two_valid_ref_and_path_splits(self):
        files = {
            ("release", "notes/SKILL.md"): SKILL_A.encode(),
            ("release/notes", "SKILL.md"): SKILL_B.encode(),
        }

        def upstream(url, *, params, raw):
            path = url.partition("/contents/")[2]
            return files[params["ref"], path]

        with patch.object(registry, "_read_remote", side_effect=upstream) as read:
            response = self._resolve(url="https://github.com/antfu/skills/blob/release/notes/SKILL.md")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], 3123)
        self.assertEqual(read.call_count, len(files))

    def test_raw_skill_file_limit_is_enforced_while_streaming(self):
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [b"abc", b"de"]
        response.__enter__.return_value = response
        with (
            patch.object(registry.requests, "get", return_value=response),
            patch.object(registry, "MAX_SKILL_FILE_BYTES", 4),
            self.assertRaises(registry.RegistryUpstreamError) as caught,
        ):
            registry._read_remote("https://api.github.com", params={}, raw=True)
        self.assertEqual((caught.exception.code, caught.exception.status), (3125, 413))
        response.__exit__.assert_called_once()

    def test_fetch_returns_single_file(self):
        tarball = _build_tar({"skills/skill-b/SKILL.md": SKILL_B})
        with self._patch_repo(tarball):
            response = self.client.post(
                f"/api/v2/spaces/{self.space.uuid}/labs/skill/registry/fetch",
                {"source": "antfu/skills", "path": "skills/skill-b/SKILL.md"},
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["name"], "skill-b")
        self.assertIn("# B", data["content"])

    def test_fetch_rejects_unsafe_path(self):
        response = self.client.post(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/registry/fetch",
            {"source": "antfu/skills", "path": "../secrets"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_requests_are_rejected(self):
        anon = APIClient()
        response = anon.get(
            f"/api/v2/spaces/{self.space.uuid}/labs/skill/registry/search",
            {"q": "vite"},
        )
        self.assertEqual(response.status_code, 401)
