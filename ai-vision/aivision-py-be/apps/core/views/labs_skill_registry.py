"""Labs Skill registry import: skills.sh search proxy and GitHub repo parsing.

Read-only endpoints behind the import dialog's remote sources. The documented
skills.sh /api/v1 needs a Vercel OIDC token we cannot mint, so search proxies
the public endpoint the skills.sh website itself uses. Repository listings
use codeload archives. Single-skill imports read GitHub files directly or
use the selected skill's published snapshot when GitHub cannot locate it.
"""

import io
import json
import re
import tarfile
import threading
import time
from urllib.parse import quote, unquote, urlparse

import requests
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ..models import Space
from .common import check_space_access_or_error, error_response, success_response

SKILLS_SH_SEARCH_URL = "https://skills.sh/api/search"
SKILLS_SH_DOWNLOAD_URL = "https://skills.sh/api/download/{owner}/{repo}/{skill_id}"
GITHUB_REPO_URL = "https://api.github.com/repos/{owner}/{repo}"
GITHUB_COMMIT_URL = GITHUB_REPO_URL + "/commits/{ref}"
GITHUB_TREE_URL = GITHUB_REPO_URL + "/git/trees/{ref}"
GITHUB_BLOB_URL = GITHUB_REPO_URL + "/git/blobs/{sha}"
GITHUB_CONTENT_URL = GITHUB_REPO_URL + "/contents/{path}"
GITHUB_RAW_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
GITHUB_TARBALL_URL = "https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}"
GITHUB_HOST = "github.com"
REQUEST_TIMEOUT = (5, 15)
TARBALL_TIMEOUT = (5, 60)

MAX_SEARCH_LIMIT = 200
# Front matter caps mirror the skill config sanitizer in labs_skill.py.
MAX_SKILL_NAME_LENGTH = 128
MAX_DESCRIPTION_LENGTH = 1024
# Registry listings are capped well above the largest known repo
# (wshobson/agents holds 185 SKILL.md files).
MAX_SKILLS_PER_REPO = 500
MAX_TARBALL_BYTES = 80 * 1024 * 1024
MAX_LOOKUP_BYTES = 8 * 1024 * 1024
MAX_SKILL_FILE_BYTES = 1024 * 1024
TARBALL_TTL_SECONDS = 600
MAX_TARBALL_CACHE_ENTRIES = 4
# Bound the ref-probing for /tree/<branch-with-slashes>/... URLs.
MAX_REF_CANDIDATES = 3

_TARBALL_CACHE: dict[tuple[str, str, str], tuple[float, bytes]] = {}
_TARBALL_LOCK = threading.Lock()


class RegistryUpstreamError(Exception):
    """An upstream (skills.sh / GitHub) failure mapped to an API error."""

    def __init__(self, message: str, code: int, status: int = 502):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _space_or_error(request, space_id: str):
    try:
        space = Space.objects.get(uuid=space_id, status=1, gmt_deleted__isnull=True)
    except Space.DoesNotExist:
        return None, error_response("Space not found", 3001, 404)
    denied = check_space_access_or_error(request.user, space)
    if denied:
        return None, denied
    return space, None


def _github_ref_candidates(segments: list[str]) -> list[tuple[str, str]]:
    """(ref, rest) pairs for the segments after /tree/ or /blob/, shortest
    ref first. Branch names may contain slashes, so the boundary between
    ref and sub path is ambiguous and resolved against the GitHub API."""
    namespace = 2 if segments[:2] in (["refs", "heads"], ["refs", "tags"]) else 0
    cuts = range(namespace + 1, min(len(segments), namespace + MAX_REF_CANDIDATES) + 1)
    return [("/".join(segments[:cut]), "/".join(segments[cut:])) for cut in cuts]


def _valid_repo(owner: str, repo: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z0-9-]+", owner)
        and re.fullmatch(r"[A-Za-z0-9_.-]+", repo)
        and repo not in (".", "..")
    )


def parse_github_target(value: str) -> dict | None:
    """Parses the accepted GitHub link shapes into owner/repo/ref/subdir.

    Accepts `owner/repo`, `https://github.com/owner/repo`,
    `.../tree/<ref>[/<subdir>]`, and `.../blob/<ref>/<path>/SKILL.md`.
    Returns None for any other host or shape; `ref` stays empty until the
    caller resolves it to the default branch.
    """
    text = (value or "").strip()
    if not text:
        return None
    if "://" not in text:
        # Bare `owner/repo` shorthand (with optional /tree/... tail): route it
        # through the github.com path parser unless a host is already given.
        parts = [seg for seg in text.split("/") if seg]
        if parts and "." not in parts[0]:
            text = f"https://github.com/{'/'.join(parts)}"
        else:
            text = f"https://{text}"
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in (
        GITHUB_HOST,
        f"www.{GITHUB_HOST}",
    ):
        return None
    segments = [unquote(seg) for seg in parsed.path.split("/") if seg]
    if len(segments) < 2 or any(
        seg in (".", "..") or "/" in seg or "\\" in seg for seg in segments
    ):
        return None
    owner, repo = segments[0], segments[1]
    if not _valid_repo(owner, repo):
        return None
    target: dict = {
        "owner": owner,
        "repo": repo,
        "ref": "",
        "kind": "",
        "tree_rest": None,
    }
    if len(segments) == 2:
        return target
    if len(segments) == 3 and segments[2] == "tree":
        # Trailing /tree without a ref; treat as the bare repo.
        return target
    kind = segments[2]
    rest = segments[3:]
    if kind == "tree":
        if not rest:
            return None
        target["kind"] = "tree"
        target["tree_rest"] = rest
        return target
    if kind == "blob":
        if not rest or rest[-1] != "SKILL.md":
            return None
        target["kind"] = "blob"
        target["tree_rest"] = rest
        return target
    return None


def _resolve_tree_rest(owner: str, repo: str, rest: list[str]) -> tuple[str, str]:
    """Splits the segments after /tree/ or /blob/ into (ref, sub path) by
    probing candidate refs against the GitHub API (404 for invalid refs)."""
    for ref, sub in _github_ref_candidates(rest):
        try:
            response = requests.get(
                GITHUB_COMMIT_URL.format(
                    owner=owner, repo=repo, ref=quote(ref, safe="")
                ),
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "aivision-skill-import"},
            )
        except requests.RequestException as exc:
            raise RegistryUpstreamError("GitHub is unreachable", 3122) from exc
        if response.status_code == 200:
            return ref, sub
        if response.status_code in (403, 429):
            raise RegistryUpstreamError(
                "GitHub API rate limit exceeded, try again later", 3122, 429
            )
        if response.status_code != 404:
            raise RegistryUpstreamError("GitHub ref lookup failed", 3122)
    raise RegistryUpstreamError("GitHub branch or path not found", 3123, 404)


def _download_tarball(owner: str, repo: str, ref: str) -> bytes:
    key = (owner, repo, ref)
    now = time.monotonic()
    with _TARBALL_LOCK:
        cached = _TARBALL_CACHE.get(key)
        if cached and now - cached[0] < TARBALL_TTL_SECONDS:
            return cached[1]
    url = GITHUB_TARBALL_URL.format(
        owner=owner, repo=repo, ref=quote(ref, safe="/")
    )
    try:
        response = requests.get(
            url,
            stream=True,
            timeout=TARBALL_TIMEOUT,
            headers={"User-Agent": "aivision-skill-import"},
        )
    except requests.RequestException as exc:
        raise RegistryUpstreamError("GitHub is unreachable", 3122) from exc
    if response.status_code != 200:
        if response.status_code == 404:
            raise RegistryUpstreamError(
                "GitHub repository or ref not found", 3122, 404
            )
        raise RegistryUpstreamError("GitHub tarball download failed", 3122)
    buffer = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            buffer.extend(chunk)
            if len(buffer) > MAX_TARBALL_BYTES:
                raise RegistryUpstreamError(
                    "Repository archive is too large to import", 3124, 413
                )
    except requests.RequestException as exc:
        raise RegistryUpstreamError("GitHub tarball download failed", 3122) from exc
    data = bytes(buffer)
    with _TARBALL_LOCK:
        while len(_TARBALL_CACHE) >= MAX_TARBALL_CACHE_ENTRIES:
            _TARBALL_CACHE.pop(next(iter(_TARBALL_CACHE)))
        _TARBALL_CACHE[key] = (time.monotonic(), data)
    return data


def _split_key(line: str) -> tuple[str, str] | None:
    if line[:1] in (" ", "\t"):
        return None
    index = line.find(":")
    if index <= 0:
        return None
    key = line[:index].strip()
    if not key:
        return None
    value = line[index + 1 :].strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    return key, value


def _is_block_indicator(value: str) -> bool:
    return value in (">", "|", ">-", "|-", ">+", "|+")


def _read_block(
    lines: list[str], start: int, end: int, folded: bool
) -> tuple[str, int]:
    body: list[str] = []
    index = start
    while index < end:
        line = lines[index]
        if not line.strip():
            body.append("")
            index += 1
            continue
        if line[:1] in (" ", "\t"):
            body.append(line.strip())
            index += 1
            continue
        break
    while body and not body[-1]:
        body.pop()
    if folded:
        folded_text = ""
        blanks = 0
        for line in body:
            if not line:
                blanks += 1
                continue
            if folded_text:
                folded_text += "\n" * blanks if blanks else " "
            folded_text += line
            blanks = 0
        return folded_text, index
    return "\n".join(body), index


def frontmatter_summary(text: str) -> tuple[str, str]:
    """(name, description) from a SKILL.md's front matter, mirroring the
    deliberately limited parser in the frontend skillConfigImport.ts."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return "", ""
    end = -1
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end < 0:
        return "", ""
    name = ""
    description = ""
    index = 1
    while index < end:
        pair = _split_key(lines[index])
        if pair is None:
            index += 1
            continue
        key, value = pair
        if key in ("name", "description"):
            if _is_block_indicator(value):
                block, index = _read_block(
                    lines, index + 1, end, value.startswith(">")
                )
                if key == "name" and not name:
                    name = block
                elif key == "description" and not description:
                    description = block
                continue
            if key == "name" and not name:
                name = value
            elif key == "description" and not description:
                description = value
        index += 1
    return name[:MAX_SKILL_NAME_LENGTH], description[:MAX_DESCRIPTION_LENGTH]


def _strip_tar_prefix(member_name: str) -> str:
    # GitHub tarballs nest everything under `<repo>-<ref>/`.
    parts = member_name.split("/", 1)
    return parts[1] if len(parts) == 2 else ""


def iter_repo_skills(tar_bytes: bytes, subdir: str = "") -> list[dict]:
    """Enumerates every SKILL.md in the archive with its front matter.

    Paths are repo-relative. `subdir` keeps only entries under it.
    """
    skills: list[dict] = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            repo_path = _strip_tar_prefix(member.name)
            if repo_path != "SKILL.md" and not repo_path.endswith("/SKILL.md"):
                continue
            if subdir and not repo_path.startswith(subdir.rstrip("/") + "/"):
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            text = extracted.read().decode("utf-8", errors="replace")
            name, description = frontmatter_summary(text)
            directory = repo_path[: -len("SKILL.md")].rstrip("/")
            skills.append(
                {
                    "path": repo_path,
                    "dirname": directory.rsplit("/", 1)[-1] if directory else "",
                    "name": name,
                    "description": description,
                }
            )
            if len(skills) >= MAX_SKILLS_PER_REPO:
                break
    skills.sort(key=lambda item: item["path"])
    return skills


def _tarball_branch(tar_bytes: bytes, repo: str) -> str:
    """Branch label parsed from the archive's `<repo>-<ref>/` root directory
    (GitHub names it after the requested ref; HEAD tarballs carry the
    literal "HEAD")."""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as archive:
        for member in archive:
            prefix = f"{repo}-"
            if member.name.startswith(prefix):
                return member.name[len(prefix) :].split("/", 1)[0] or "HEAD"
            break
    return "HEAD"


def _read_tar_member(tar_bytes: bytes, path: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            if _strip_tar_prefix(member.name) == path:
                extracted = archive.extractfile(member)
                if extracted is None:
                    break
                return extracted.read().decode("utf-8", errors="replace")
    raise RegistryUpstreamError("SKILL.md not found in repository", 3123, 404)


def _read_remote(url: str, *, params: dict, raw: bool = False) -> bytes:
    limit = MAX_SKILL_FILE_BYTES if raw else MAX_LOOKUP_BYTES
    try:
        with requests.get(
            url,
            params=params,
            stream=True,
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": "aivision-skill-import",
                "Accept": (
                    "application/vnd.github.raw+json" if raw else "application/json"
                ),
            },
        ) as response:
            if response.status_code in (403, 429):
                raise RegistryUpstreamError("Upstream rate limit exceeded", 3122, 429)
            if response.status_code == 404:
                raise RegistryUpstreamError("Upstream resource not found", 3123, 404)
            if response.status_code != 200:
                raise RegistryUpstreamError("Upstream request failed", 3122)
            data = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                data.extend(chunk)
                if len(data) > limit:
                    if raw:
                        raise RegistryUpstreamError("SKILL.md is too large to import", 3125, 413)
                    raise RegistryUpstreamError("Upstream lookup is too large", 3123, 422)
    except requests.RequestException as exc:
        raise RegistryUpstreamError("Upstream is unreachable", 3122) from exc
    return bytes(data)


def _skill_entry(path: str, content: bytes) -> dict:
    text = content.decode("utf-8", errors="replace")
    name, description = frontmatter_summary(text)
    return {
        "path": path,
        "dirname": path.split("/")[-2] if "/" in path else "",
        "name": name,
        "description": description,
        "content": text,
    }


def _matches_skill_name(entry: dict, skill_id: str) -> bool:
    name = entry["name"].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return skill_id in (name, slug)


def _resolve_registry_skill(owner: str, repo: str, ref: str, skill_id: str) -> dict | None:
    tree_url = GITHUB_TREE_URL.format(owner=owner, repo=repo, ref=quote(ref, safe=""))
    try:
        body = json.loads(_read_remote(tree_url, params={"recursive": "1"}))
    except ValueError as exc:
        raise RegistryUpstreamError("Invalid GitHub tree response", 3122) from exc
    if not isinstance(body, dict) or not isinstance(body.get("tree"), list):
        raise RegistryUpstreamError("Invalid GitHub tree response", 3122)
    unresolved = RegistryUpstreamError(
        "Cannot uniquely locate the selected SKILL.md", 3123, 422
    )
    if body.get("truncated") is not False:
        raise unresolved

    candidates = []
    root = None
    for item in body["tree"]:
        if (
            not isinstance(item, dict)
            or item.get("type") != "blob"
            or item.get("mode") not in ("100644", "100755")
        ):
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        parts = path.split("/")
        if path == "SKILL.md":
            root = item
        elif parts[-1] == "SKILL.md" and parts[-2].lower() == skill_id:
            candidates.append(item)
    if len(candidates) > 1:
        raise unresolved
    if root is not None:
        candidates.append(root)

    selected = None
    for candidate in candidates:
        sha = candidate.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RegistryUpstreamError("Invalid GitHub blob reference", 3122)
        blob_url = GITHUB_BLOB_URL.format(owner=owner, repo=repo, sha=sha)
        entry = _skill_entry(candidate["path"], _read_remote(blob_url, params={}, raw=True))
        if _matches_skill_name(entry, skill_id):
            if selected is not None:
                raise unresolved
            selected = entry
    return selected


def _read_registry_snapshot(owner: str, repo: str, skill_id: str) -> dict:
    url = SKILLS_SH_DOWNLOAD_URL.format(
        owner=owner, repo=repo, skill_id=quote(skill_id, safe="")
    )
    body = json.loads(_read_remote(url, params={}))
    files = body.get("files") if isinstance(body, dict) else None
    if not isinstance(files, list):
        raise ValueError("Registry snapshot is missing a file list")
    contents = [
        item.get("contents")
        for item in files
        if isinstance(item, dict) and item.get("path") == "SKILL.md"
    ]
    if len(contents) != 1 or not isinstance(contents[0], str):
        raise ValueError("Registry snapshot is missing a unique SKILL.md")
    content = contents[0].encode("utf-8")
    if len(content) > MAX_SKILL_FILE_BYTES:
        raise RegistryUpstreamError("SKILL.md is too large to import", 3125, 413)
    entry = _skill_entry("SKILL.md", content)
    if not _matches_skill_name(entry, skill_id):
        raise ValueError("Registry snapshot does not match the selected skill")
    return {**entry, "registrySnapshot": True}


def _resolve_selected_skill(owner: str, repo: str, ref: str, skill_id: str) -> tuple[str, dict]:
    lookup_ref = ref or "HEAD"
    try:
        entry = _resolve_registry_skill(owner, repo, lookup_ref, skill_id)
    except RegistryUpstreamError as exc:
        if exc.code != 3122 and (exc.code, exc.status) != (3123, 404):
            raise
        failure = exc
    else:
        if entry is not None:
            return lookup_ref, entry
        failure = RegistryUpstreamError(
            "Cannot uniquely locate the selected SKILL.md", 3123, 422
        )

    # A published snapshot has no verified Git revision or repository path.
    if ref or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", skill_id):
        raise failure
    try:
        return "", _read_registry_snapshot(owner, repo, skill_id)
    except RegistryUpstreamError as exc:
        if exc.code == 3125:
            raise
        raise failure from exc
    except ValueError as exc:
        raise failure from exc


def _resolve_blob_link(owner: str, repo: str, rest: list[str]) -> tuple[str, dict]:
    candidates = [(ref, path) for ref, path in _github_ref_candidates(rest) if path]
    if candidates and re.fullmatch(r"[0-9a-f]{40}", candidates[0][0]):
        candidates = candidates[:1]
    if len(candidates) == 1:
        ref, path = candidates[0]
        raw_url = GITHUB_RAW_URL.format(
            owner=owner, repo=repo, ref=quote(ref, safe=""), path=quote(path, safe="/")
        )
        try:
            content = _read_remote(raw_url, params={}, raw=True)
        except RegistryUpstreamError as exc:
            if exc.code not in (3122, 3123):
                raise
        else:
            return ref, _skill_entry(path, content)

    # Raw GitHub can resolve a different split of the same URL and still return
    # 200. The Contents API verifies the ref and path as separate parameters.
    selected = None
    for ref, path in candidates:
        api_url = GITHUB_CONTENT_URL.format(owner=owner, repo=repo, path=quote(path, safe="/"))
        try:
            content = _read_remote(api_url, params={"ref": ref}, raw=True)
        except RegistryUpstreamError as exc:
            if exc.status == 404:
                continue
            raise
        if selected is not None:
            raise RegistryUpstreamError("GitHub file link matches multiple refs", 3123, 422)
        selected = (ref, _skill_entry(path, content))
    if selected is not None:
        return selected
    raise RegistryUpstreamError("GitHub file or ref not found", 3123, 404)


def _repo_target(payload_source: object, payload_ref: object) -> dict:
    source = str(payload_source or "").strip()
    ref = str(payload_ref or "").strip()
    if "/" not in source:
        return {}
    owner, _, repo = source.partition("/")
    if not _valid_repo(owner, repo):
        return {}
    return {"owner": owner, "repo": repo, "ref": ref, "kind": "", "tree_rest": None}


def _resolve_target(target: dict) -> tuple[str, str, str, str]:
    """Returns (owner, repo, ref, sub): unpinned links resolve to the literal
    "HEAD" (the codeload archive endpoint expands it to the default branch
    itself, sparing the rate-limited GitHub API); sub is the path below the
    ref (the subdir for tree links, the SKILL.md path for blob links)."""
    owner, repo = target["owner"], target["repo"]
    tree_rest = target.get("tree_rest")
    if tree_rest:
        ref, sub = _resolve_tree_rest(owner, repo, tree_rest)
        return owner, repo, ref, sub
    ref = target.get("ref") or "HEAD"
    return owner, repo, ref, ""


class SkillRegistrySearchView(APIView):
    """GET: proxy the public skills.sh search endpoint."""

    permission_classes = [IsAuthenticated]

    def get(self, request, space_id: str):
        _, denied = _space_or_error(request, space_id)
        if denied:
            return denied
        query = str(request.query_params.get("q", "")).strip()
        if len(query) < 2:
            return error_response(
                "Search query must be at least 2 characters", 3101
            )
        try:
            limit = int(request.query_params.get("limit", ""))
        except ValueError:
            limit = MAX_SEARCH_LIMIT
        limit = max(1, min(limit, MAX_SEARCH_LIMIT))
        try:
            upstream = requests.get(
                SKILLS_SH_SEARCH_URL,
                params={"q": query, "limit": limit},
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "aivision-skill-import"},
            )
        except requests.RequestException:
            return error_response("skills.sh search failed", 3121, 502)
        if upstream.status_code != 200:
            return error_response("skills.sh search failed", 3121, 502)
        try:
            body = upstream.json()
        except ValueError:
            return error_response("skills.sh search failed", 3121, 502)
        rows = body.get("skills") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            rows = []
        skills = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            skill_id = str(row.get("id") or "").strip()
            if not skill_id:
                continue
            try:
                installs = int(row.get("installs") or 0)
            except (TypeError, ValueError):
                installs = 0
            skills.append(
                {
                    "id": skill_id,
                    "source": str(row.get("source") or ""),
                    "skillId": str(row.get("skillId") or ""),
                    "name": str(row.get("name") or ""),
                    "installs": installs,
                    "url": f"https://skills.sh/{skill_id}",
                }
            )
        return success_response(
            {
                "query": query,
                "searchType": str(body.get("searchType") or ""),
                "count": len(skills),
                "skills": skills,
            }
        )


class SkillRegistryResolveView(APIView):
    """POST: list repository skills, or return a uniquely selected SKILL.md."""

    permission_classes = [IsAuthenticated]

    def post(self, request, space_id: str):
        _, denied = _space_or_error(request, space_id)
        if denied:
            return denied
        payload = request.data if isinstance(request.data, dict) else {}
        url_value = str(payload.get("url") or "").strip()
        skill_id = str(payload.get("skillId") or "").strip().lower()
        if "skillId" in payload and (
            skill_id in ("", ".", "..") or len(skill_id) > MAX_SKILL_NAME_LENGTH
        ):
            return error_response("A valid Skill ID is required", 3101)
        if url_value:
            target = parse_github_target(url_value)
            if target is None:
                return error_response(
                    "Unsupported link. Use a github.com repository, tree, or SKILL.md blob URL.",
                    3101,
                )
        else:
            target = _repo_target(payload.get("source"), payload.get("ref"))
            if not target:
                return error_response("A GitHub repository source is required", 3101)
        try:
            owner, repo = target["owner"], target["repo"]
            if target.get("kind") == "blob":
                branch, entry = _resolve_blob_link(owner, repo, target["tree_rest"])
                skills, subdir = [entry], ""
            elif not url_value and skill_id:
                branch, entry = _resolve_selected_skill(owner, repo, target["ref"], skill_id)
                skills, subdir = [entry], ""
            else:
                owner, repo, ref, subdir = _resolve_target(target)
                tar_bytes = _download_tarball(owner, repo, ref)
                branch = _tarball_branch(tar_bytes, repo) if ref == "HEAD" else ref
                skills = iter_repo_skills(tar_bytes, subdir=subdir)
        except RegistryUpstreamError as exc:
            return error_response(exc.message, exc.code, exc.status)
        return success_response(
            {
                "source": f"{owner}/{repo}",
                "branch": branch,
                "subdir": subdir,
                "skills": skills,
            }
        )


class SkillRegistryFetchView(APIView):
    """POST: return one SKILL.md's full text from a GitHub repository."""

    permission_classes = [IsAuthenticated]

    def post(self, request, space_id: str):
        _, denied = _space_or_error(request, space_id)
        if denied:
            return denied
        payload = request.data if isinstance(request.data, dict) else {}
        target = _repo_target(payload.get("source"), payload.get("ref"))
        if not target:
            return error_response("A GitHub repository source is required", 3101)
        path = str(payload.get("path") or "").strip().strip("/")
        if (
            not path
            or not path.endswith("SKILL.md")
            or ".." in path.split("/")
            or path.startswith("/")
        ):
            return error_response("A repository-relative SKILL.md path is required", 3101)
        try:
            owner, repo, ref = _resolve_target(target)[:3]
            tar_bytes = _download_tarball(owner, repo, ref)
            content = _read_tar_member(tar_bytes, path)
        except RegistryUpstreamError as exc:
            return error_response(exc.message, exc.code, exc.status)
        name, description = frontmatter_summary(content)
        return success_response(
            {
                "path": path,
                "name": name,
                "description": description,
                "content": content,
            }
        )
