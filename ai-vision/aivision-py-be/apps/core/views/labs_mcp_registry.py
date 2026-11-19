"""Read-only official and MCP servers directory discovery and import previews.

Only fixed registry endpoints are fetched. Preview commands are data, never run;
clients must review them and substitute fields recursively before importing.
"""

import gzip
import hashlib
import io
import ipaddress
import json
import re
import tarfile
import time
import zlib
from urllib.parse import quote, unquote, urlsplit

import requests
from django.core.cache import cache
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from .common import error_response, success_response
from .labs_skill_registry import _space_or_error

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0.1/servers"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CURSOR_LENGTH = 2048
CACHE_SECONDS = 60
NAMESPACE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9.-]*")
SERVER_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*")
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_.-]*)\}")
SECRET = re.compile(r"secret|token|password|authorization|api[-_]?key", re.I)


OFFICIAL_UNAVAILABLE = "The official MCP registry could not be loaded. Try again later."
DIRECTORY_UNAVAILABLE = "The MCP servers directory could not be loaded. Try again later."


class RegistryError(Exception):
    pass


class DirectoryCursorError(ValueError):
    pass


class UnsupportedConfig(Exception):
    pass


def _public_url(value, *, template=False):
    """Reject non-web, credentialed, local and literal non-public IP links.

    No DNS resolution or destination fetch occurs here. Templates are allowed
    only outside the authority so user input cannot select a private host.
    """
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    decoded = unquote(value)
    if any(ord(c) <= 32 or ord(c) == 127 for c in decoded) or "\\" in decoded:
        return ""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or any(c in parsed.netloc for c in "{}%")
            or parsed.port == 0
        ):
            return ""
        host = host.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            # URL clients treat a numeric final label as an IPv4 candidate,
            # including hexadecimal, octal and abbreviated legacy forms.
            last_label = host.rsplit(".", 1)[-1]
            if "." not in host or re.fullmatch(r"[0-9]+|0x[0-9a-f]*", last_label):
                return ""
            if not re.fullmatch(r"[a-z0-9.-]+", host):
                return ""
        if not template and ("{" in value or "}" in value):
            return ""
        return value
    except ValueError:
        return ""


def _fetch(path="", params=None):
    key = "labs-mcp-registry:" + hashlib.sha256(
        json.dumps([path, params], sort_keys=True).encode()
    ).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            REGISTRY_URL + path,
            params=params,
            headers={"Accept": "application/json"},
            timeout=(3, 10),
            allow_redirects=False,
            stream=True,
        )
        try:
            if response.status_code != 200:
                raise RegistryError("Official MCP registry request failed")
            chunks = []
            size = 0
            deadline = time.monotonic() + 15
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                    raise RegistryError("Official MCP registry response exceeded limits")
                chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
            if not isinstance(payload, dict):
                raise RegistryError("Malformed official MCP registry response")
        finally:
            response.close()
    except (requests.RequestException, ValueError, RecursionError) as exc:
        raise RegistryError("Official MCP registry is unavailable or returned invalid JSON") from exc
    # Validation happens before caching, including on cache hits at the caller.
    return key, payload


def _registry_name(value):
    if not isinstance(value, str) or len(value) > 200:
        return False
    namespace, delimiter, name = value.partition("/")
    return bool(delimiter and NAMESPACE.fullmatch(namespace) and SERVER_NAME.fullmatch(name))


def _server(payload):
    server = payload.get("server") if isinstance(payload, dict) else None
    if (
        not isinstance(server, dict)
        or not _registry_name(server.get("name"))
        or not isinstance(server.get("description"), str)
        or not isinstance(server.get("version"), str)
    ):
        raise RegistryError("Malformed official MCP registry server")
    for key in ("remotes", "packages"):
        if key in server and (
            not isinstance(server[key], list)
            or not all(isinstance(item, dict) for item in server[key])
        ):
            raise RegistryError("Malformed official MCP registry configurations")
    return server


def _summary(server):
    repository = server.get("repository")
    repo = _public_url(repository.get("url")) if isinstance(repository, dict) else ""
    result = {
        "id": server["name"],
        "name": server.get("title") if isinstance(server.get("title"), str) else server["name"],
        "description": server["description"],
        "url": _public_url(server.get("websiteUrl")) or repo or (
            REGISTRY_URL + "/" + quote(server["name"], safe="") + "/versions/latest"
        ),
        "source": "official",
    }
    if repo:
        result["repositoryUrl"] = repo
    return result


class _Template:
    def __init__(self):
        self.fields = {}

    def field(self, key, spec, *, label=None):
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*", key):
            raise UnsupportedConfig("Unsupported input field name")
        field = {
            "key": key,
            "label": label or key,
            "required": bool(spec.get("isRequired", False)),
            "secret": bool(spec.get("isSecret", False)) or bool(SECRET.search(key)),
        }
        if isinstance(spec.get("description"), str):
            field["description"] = spec["description"]
        if isinstance(spec.get("default"), str) and not field["secret"]:
            field["default"] = spec["default"]
        previous = self.fields.get(key)
        if previous and previous != field:
            raise UnsupportedConfig("Conflicting input field definitions")
        self.fields[key] = field
        return "{" + key + "}"

    def value(self, spec, key):
        if not isinstance(spec, dict):
            raise UnsupportedConfig("Invalid input definition")
        value = spec.get("value")
        if value is None:
            return self.field(key, spec)
        if not isinstance(value, str):
            raise UnsupportedConfig("Invalid input value")
        variables = spec.get("variables", {})
        if not isinstance(variables, dict):
            raise UnsupportedConfig("Invalid input variables")
        for match in PLACEHOLDER.finditer(value):
            name = match.group(1)
            definition = variables.get(name, spec)
            if not isinstance(definition, dict):
                raise UnsupportedConfig("Invalid input variable")
            if name in variables and "value" in definition:
                fixed = definition["value"]
                if not isinstance(fixed, str) or "{" in fixed or "}" in fixed:
                    raise UnsupportedConfig("Unsupported fixed input variable")
                value = value.replace("{" + name + "}", fixed)
                continue
            # Missing variable declarations occur in real registry headers.
            # Inherit container requirements and secrecy even for declared vars.
            definition = {
                **definition,
                "isRequired": bool(definition.get("isRequired")) or bool(spec.get("isRequired")),
                "isSecret": bool(definition.get("isSecret")) or bool(spec.get("isSecret")),
            }
            self.field(name, definition)
        residual = PLACEHOLDER.sub("", value)
        if "{" in residual or "}" in residual:
            raise UnsupportedConfig("Unsupported template syntax")
        return value

    def mapping(self, items, prefix):
        if not isinstance(items, list):
            raise UnsupportedConfig("Invalid input list")
        result = {}
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise UnsupportedConfig("Invalid named input")
            name = item["name"]
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", name) or name in result:
                raise UnsupportedConfig("Unsupported or duplicate input name")
            spec = {**item, "isSecret": bool(item.get("isSecret")) or bool(SECRET.search(name))}
            result[name] = self.value(spec, f"{prefix}_{index}")
        return result

    def arguments(self, items):
        if not isinstance(items, list):
            raise UnsupportedConfig("Invalid arguments")
        result = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("isRepeated"):
                raise UnsupportedConfig("Repeated arguments need manual configuration")
            kind = item.get("type")
            if kind == "named":
                name = item.get("name")
                if not isinstance(name, str) or not re.fullmatch(r"--?[a-zA-Z0-9_-]+", name):
                    raise UnsupportedConfig("Invalid named argument")
                result.append(name)
            elif kind != "positional":
                raise UnsupportedConfig("Unsupported argument type")
            if "value" not in item and not item.get("isRequired"):
                # Optional argv elements cannot be conditionally omitted by the
                # frontend's string-only placeholder substitution contract.
                raise UnsupportedConfig("Optional arguments need manual configuration")
            result.append(self.value(item, f"arg_{index}"))
        return result


def _remote(remote, template):
    transport = {"streamable-http": "http", "sse": "sse"}.get(remote.get("type"))
    url = _public_url(remote.get("url"), template=True)
    if not transport or not url:
        raise UnsupportedConfig("Unsupported transport or non-public remote URL")
    # Remote URL variables have no input definitions in this schema. Prompt
    # explicitly, but never allow variable substitution in the URL authority.
    residual = PLACEHOLDER.sub("", url)
    if "{" in residual or "}" in residual:
        raise UnsupportedConfig("Unsupported remote URL template")
    for name in PLACEHOLDER.findall(url):
        template.field(name, {"isRequired": True})
    config = {"type": transport, "url": url}
    headers = template.mapping(remote.get("headers", []), "header")
    if headers:
        config["headers"] = headers
    return config


def _pinned_version(value):
    if not isinstance(value, str):
        return False
    core, *suffix = re.split(r"[-+]", value, maxsplit=1)
    components = core.split(".", 3)
    return (
        len(components) == 3
        and all(component.isascii() and component.isdecimal() for component in components)
        and (not suffix or bool(re.fullmatch(r"[a-zA-Z0-9.-]+", suffix[0])))
    )


def _package(package, template):
    if package.get("transport", {}).get("type") != "stdio":
        raise UnsupportedConfig("Only stdio packages can be converted reliably")
    kind = package.get("registryType")
    identifier = package.get("identifier")
    version = package.get("version")
    if not _pinned_version(version):
        raise UnsupportedConfig("A pinned package version is required")
    if kind == "pypi":
        # runtimeHint='uvx' names the runner, not the console entry point.
        # The official schema does not declare that entry point; do not guess
        # it from a distribution name (hyphens and executable names may differ).
        raise UnsupportedConfig("PyPI console entry point is not declared; configure uvx --from manually")
    if (
        kind != "npm"
        or package.get("runtimeHint", "npx") != "npx"
        or package.get("registryBaseUrl", "https://registry.npmjs.org").rstrip("/") != "https://registry.npmjs.org"
        or not isinstance(identifier, str)
        or not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9][a-z0-9._-]*", identifier)
    ):
        raise UnsupportedConfig("Unsupported package registry or runtime")
    if package.get("runtimeArguments"):
        raise UnsupportedConfig("Custom runtime arguments need manual review")
    return {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", f"{identifier}@{version}", *template.arguments(package.get("packageArguments", []))],
        "env": template.mapping(package.get("environmentVariables", []), "env"),
    }


def _detail(server):
    summary = _summary(server)
    summary.pop("repositoryUrl", None)
    options = []
    reasons = []
    for kind, items in (("remote", server.get("remotes", [])), ("package", server.get("packages", []))):
        for index, item in enumerate(items):
            template = _Template()
            try:
                config = _remote(item, template) if kind == "remote" else _package(item, template)
                config["description"] = server["description"]
                options.append({
                    "id": f"{kind}-{index}",
                    "label": f"{config['type']} · {config.get('url') or item.get('identifier')}",
                    "config": config,
                    "fields": list(template.fields.values()),
                })
            except (UnsupportedConfig, AttributeError, TypeError) as exc:
                reasons.append(str(exc) if isinstance(exc, UnsupportedConfig) else "Malformed configuration")
    summary["options"] = options
    if not options:
        summary["unsupportedReason"] = "; ".join(dict.fromkeys(reasons)) or "No supported installation configuration is published"
    return summary


MCPSERVERS_ARCHIVE_URL = "https://codeload.github.com/wong2/awesome-mcp-servers/tar.gz/HEAD"
MCPSERVERS_SOURCE_URL = "https://github.com/wong2/awesome-mcp-servers"
MCPSERVERS_CACHE_KEY = "labs-mcpservers-directory:v1"
MCPSERVERS_CACHE_SECONDS = 600
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_TAR_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000
MAX_README_BYTES = 2 * 1024 * 1024
MAX_LICENSE_BYTES = 16384
MCPSERVERS_ID = re.compile(r"[a-f0-9]{64}\Z")
# Keep the full upstream notice with this README-derived parser and require it
# in downloaded copies rather than labeling arbitrary license text as MIT.
MCPSERVERS_LICENSE_TEXT = """MIT License

Copyright (c) 2024 wong2

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
SERVER_SECTIONS = {"Reference Servers", "Official Servers", "Community Servers"}
def _markdown_destination_end(text, start):
    """Return the matching delimiter in one pass, with no backtracking."""
    nesting = 0
    for end in range(start, len(text)):
        char = text[end]
        if char == "(":
            nesting += 1
        elif char == ")":
            if nesting == 0:
                return end if end > start else None
            nesting -= 1
        elif char.isspace():
            return None
    return None


def _server_bullet(line):
    """Read a directory link, consecutive badges, and its description."""
    if not line.startswith("- ") or len(line) > 24_576:
        return None
    start = 4 if line.startswith("- **") else 2
    if not line.startswith("[", start):
        return None
    label_end = line.find("](", start + 1)
    if label_end == -1:
        return None
    name = line[start + 1 : label_end]
    if not name or "[" in name or "]" in name:
        return None
    url_start = label_end + 2
    url_end = _markdown_destination_end(line, url_start)
    if url_end is None:
        return None
    cursor = url_end + 1
    if line.startswith("**", cursor):
        cursor += 2
    while cursor < len(line):
        before_space = cursor
        while cursor < len(line) and line[cursor].isspace():
            cursor += 1
        if cursor + 1 < len(line) and line[cursor] in "-–" and line[cursor + 1].isspace():
            description = line[cursor + 2 :].lstrip()
            if cursor > before_space and description:
                return name, line[url_start:url_end], description
            return None
        linked = line.startswith("[![", cursor)
        if not linked and not line.startswith("![", cursor):
            return None
        label_end = line.find("](", cursor + (3 if linked else 2))
        if label_end == -1:
            return None
        badge_end = _markdown_destination_end(line, label_end + 2)
        if badge_end is None:
            return None
        cursor = badge_end + 1
        if linked:
            if not line.startswith("](", cursor):
                return None
            target_end = _markdown_destination_end(line, cursor + 2)
            if target_end is None:
                return None
            cursor = target_end + 1
    return None


def _mcpservers_parse(readme):
    entries = {}
    section = None
    fenced = False
    for line in readme.splitlines():
        if line.startswith(("```", "~~~")):
            fenced = not fenced
        if fenced:
            continue
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("# "):
            section = None
        if section not in SERVER_SECTIONS:
            continue
        parsed = _server_bullet(line)
        if parsed is None:
            continue
        name, url, description = parsed
        url = _public_url(url)
        if not url or not name.strip() or len(name) > 200 or len(description) > 10000:
            continue
        identifier = hashlib.sha256(url.encode()).hexdigest()
        entry = {
            "id": identifier, "name": name, "description": description,
            "source": "mcpservers", "url": url,
        }
        if urlsplit(url).hostname.lower() == "github.com":
            entry["repositoryUrl"] = url
        entries.setdefault(identifier, entry)
    if not entries:
        raise RegistryError("Malformed MCP servers directory README")
    return entries


def _mcpservers_archive(raw):
    # Bound decompression before getmembers(), including ignored files and tar
    # metadata. Never extract paths or follow symlinks from an upstream archive.
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        tar_bytes = compressed.read(MAX_TAR_BYTES + 1)
    if len(tar_bytes) > MAX_TAR_BYTES:
        raise RegistryError("MCP servers directory archive exceeded limits")
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
        # Iterate first to bound member allocation before getmembers().
        for index, _ in enumerate(archive):
            if index >= MAX_ARCHIVE_MEMBERS:
                raise RegistryError("MCP servers directory has too many files")
        files = {}
        for member in archive.getmembers():
            parts = member.name.split("/")
            if len(parts) != 2 or not parts[0].startswith("awesome-mcp-servers-"):
                continue
            name = parts[1]
            if name not in {"README.md", "LICENSE"}:
                continue
            limit = MAX_README_BYTES if name == "README.md" else MAX_LICENSE_BYTES
            if name in files or not member.isfile() or member.size > limit:
                raise RegistryError("Malformed MCP servers directory archive")
            with archive.extractfile(member) as source:
                data = source.read(limit + 1)
            if len(data) > limit:
                raise RegistryError("MCP servers directory file exceeded limits")
            files[name] = data.decode("utf-8")
    if set(files) != {"README.md", "LICENSE"}:
        raise RegistryError("MCP servers directory README or license is missing")
    license_text = files["LICENSE"]
    if license_text.strip() != MCPSERVERS_LICENSE_TEXT.strip():
        raise RegistryError("Unexpected MCP servers directory license")
    return {
        "entries": _mcpservers_parse(files["README.md"]),
        "attribution": {
            "name": "Awesome MCP Servers", "url": MCPSERVERS_SOURCE_URL,
            "license": "MIT", "licenseText": license_text,
            "directoryUrl": "https://mcpservers.org/",
        },
    }


def _mcpservers_catalog():
    cached = cache.get(MCPSERVERS_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            MCPSERVERS_ARCHIVE_URL, headers={"Accept": "application/gzip"},
            timeout=(3, 10), allow_redirects=False, stream=True,
        )
        try:
            if response.status_code != 200:
                raise RegistryError("MCP servers directory request failed")
            chunks, size = [], 0
            deadline = time.monotonic() + 15
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES or time.monotonic() > deadline:
                    raise RegistryError("MCP servers directory response exceeded limits")
                chunks.append(chunk)
            catalog = _mcpservers_archive(b"".join(chunks))
        finally:
            response.close()
    except (requests.RequestException, tarfile.TarError, OSError, EOFError, ValueError, zlib.error) as exc:
        raise RegistryError("MCP servers directory is unavailable or malformed") from exc
    cache.set(MCPSERVERS_CACHE_KEY, catalog, MCPSERVERS_CACHE_SECONDS)
    return catalog


def _mcpservers_cursor(query, catalog, offset):
    digest = hashlib.sha256(json.dumps([query.casefold(), catalog], sort_keys=True).encode()).hexdigest()[:24]
    return f"{digest}:{offset}"


def _mcpservers_search(query, cursor):
    catalog = _mcpservers_catalog()
    offset = 0
    if cursor:
        match = re.fullmatch(r"[a-f0-9]{24}:([0-9]{1,5})", cursor)
        if not match:
            raise DirectoryCursorError("Invalid MCP servers directory cursor")
        offset = int(match.group(1))
        if offset == 0 or offset % 30 or cursor != _mcpservers_cursor(query, catalog, offset):
            raise DirectoryCursorError("Invalid or stale MCP servers directory cursor")
    matches = [
        entry for _, entry in sorted(catalog["entries"].items())
        if query.casefold() in (entry["name"] + " " + entry["description"]).casefold()
    ]
    if cursor and offset >= len(matches):
        raise DirectoryCursorError("MCP servers directory cursor is out of range")
    return {
        "servers": matches[offset:offset + 30],
        "nextCursor": _mcpservers_cursor(query, catalog, offset + 30) if offset + 30 < len(matches) else None,
        "attribution": catalog["attribution"],
    }


class McpRegistrySearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, space_id):
        _, denied = _space_or_error(request, space_id)
        if denied is not None:
            return denied
        query = request.query_params.get("q", "").strip()
        cursor = request.query_params.get("cursor", "")
        source = request.query_params.get("source", "official")
        if source not in {"official", "mcpservers"} or len(query) > 200 or len(cursor) > MAX_CURSOR_LENGTH:
            return error_response("Invalid registry source, query or cursor", 3003)
        if source == "mcpservers":
            try:
                return success_response(_mcpservers_search(query, cursor))
            except DirectoryCursorError:
                return error_response(
                    "Invalid or stale MCP servers directory cursor. Start a new search.",
                    3003,
                )
            except RegistryError:
                return error_response(DIRECTORY_UNAVAILABLE, 3030, 502)
        params = {"search": query, "limit": 30, "version": "latest"}
        if cursor:
            params["cursor"] = cursor
        try:
            key, payload = _fetch(params=params)
            servers = payload.get("servers")
            metadata = payload.get("metadata")
            if not isinstance(servers, list) or not isinstance(metadata, dict):
                raise RegistryError("Malformed official MCP registry search response")
            next_cursor = metadata.get("nextCursor")
            if next_cursor is not None and (not isinstance(next_cursor, str) or len(next_cursor) > MAX_CURSOR_LENGTH):
                raise RegistryError("Malformed official MCP registry cursor")
            result = {"servers": [_summary(_server(item)) for item in servers], "nextCursor": next_cursor or None}
            cache.add(key, (key, payload), CACHE_SECONDS)
            return success_response(result)
        except RegistryError:
            return error_response(OFFICIAL_UNAVAILABLE, 3030, 502)


class McpRegistryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, space_id):
        _, denied = _space_or_error(request, space_id)
        if denied is not None:
            return denied
        identifier = request.query_params.get("id", "")
        source = request.query_params.get("source", "official")
        valid_id = (
            len(identifier) == 64 and MCPSERVERS_ID.fullmatch(identifier)
            if source == "mcpservers"
            else _registry_name(identifier)
        )
        if source not in {"official", "mcpservers"} or not valid_id:
            return error_response("Invalid registry source or server id", 3003)
        try:
            if source == "mcpservers":
                catalog = _mcpservers_catalog()
                entry = catalog["entries"].get(identifier)
                if entry is None:
                    return error_response("MCP servers directory entry not found", 3004, 404)
                return success_response({
                    **entry, "options": [], "unsupportedReason": "directory_only",
                    "attribution": catalog["attribution"],
                })
            key, payload = _fetch("/" + quote(identifier, safe="") + "/versions/latest")
            server = _server(payload)
            if server["name"] != identifier:
                raise RegistryError("Official MCP registry returned an unexpected server")
            result = _detail(server)
            cache.add(key, (key, payload), CACHE_SECONDS)
            return success_response(result)
        except RegistryError:
            message = DIRECTORY_UNAVAILABLE if source == "mcpservers" else OFFICIAL_UNAVAILABLE
            return error_response(message, 3030, 502)
