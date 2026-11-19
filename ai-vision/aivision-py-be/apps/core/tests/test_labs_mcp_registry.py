"""Fixture-only discovery tests; no external registry or MCP calls."""

import copy
import json
import io
import tarfile
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import requests
from django.core.cache import cache
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.core.models import Space
from apps.core.tests.helpers import SpaceKeyTestCase, create_local_user, create_test_space_member
from apps.core.views.labs_mcp_registry import (
    MAX_RESPONSE_BYTES,
    REGISTRY_URL,
    MCPSERVERS_ARCHIVE_URL,
    MCPSERVERS_CACHE_KEY,
    MAX_ARCHIVE_BYTES,
    MAX_README_BYTES,
    _mcpservers_parse,
    _mcpservers_archive,
    RegistryError,
    _mcpservers_catalog,
    _detail,
    _fetch,
    _public_url,
)

SERVER = {
    "name": "io.github.example/tools",
    "description": "Example tools",
    "version": "1.0.0",
    "repository": {"url": "https://github.com/example/tools"},
    "remotes": [{
        "type": "streamable-http",
        "url": "https://mcp.example.com/mcp",
        "headers": [{
            "name": "Authorization", "isRequired": True, "isSecret": True,
            "value": "Bearer {smithery_api_key}",
        }],
    }],
}


# Representative upstream README bullets; copied text retains the MIT notice.
LICENSE_TEXT = 'MIT License\n\nCopyright (c) 2024 wong2\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the "Software"), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\nSOFTWARE.\n'
README = """# Awesome MCP Servers
## Sponsors
- **[Sponsor](https://sponsor.example.com)** - Not a curated entry
## Reference Servers
- **[Everything](https://github.com/modelcontextprotocol/servers/blob/main/src/everything)** - Reference / test server with prompts, resources, and tools
- **[Fetch](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch)** - Web content fetching and conversion for efficient LLM usage
## Official Servers
- **[Agent Mindshare](https://agentmindshare.com)** - Track and monitor AI agent mindshare across platforms
## Community Servers
- **[Same](https://github.com/example/one)** ![badge](https://img.example.com/a) - First project
- **[Same](https://github.com/example/two)** [![badge](https://img.example.com/a)](https://example.com) - Second project
## Clients
- **[Client](https://client.example.com)** - Not a server
## Frameworks
- **[Framework](https://framework.example.com)** - Not a server
"""


def archive_bytes(readme=README, license_text=LICENSE_TEXT, extras=()):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, value in [("README.md", readme), ("LICENSE", license_text), *extras]:
            data = value.encode()
            member = tarfile.TarInfo("awesome-mcp-servers-HEAD/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def directory_response(readme=README):
    result = Mock(status_code=200)
    result.iter_content.return_value = [archive_bytes(readme)]
    return result


class DirectoryDiscoveryTests(SimpleTestCase):
    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_curated_sections_badges_and_url_identity(self):
        entries = _mcpservers_parse(README)
        self.assertEqual(len(entries), 5)
        same = [entry for entry in entries.values() if entry["name"] == "Same"]
        self.assertEqual(len(same), 2)
        self.assertNotEqual(same[0]["id"], same[1]["id"])
        self.assertEqual(entries, _mcpservers_parse(README))
        for entry in entries.values():
            self.assertEqual(entry["source"], "mcpservers")
            self.assertEqual("repositoryUrl" in entry, urlsplit(entry["url"]).hostname == "github.com")
        for url in ["http://localhost/a", "http://127.0.0.1/a", "https://user:pass@example.com/a", "javascript:evil"]:
            with self.assertRaises(RegistryError):
                _mcpservers_parse("## Official Servers\n- **[Unsafe](" + url + ")** - Unsafe")
        for text in ["", "<html>bad</html>", "## Sponsors\n- **[Other](https://example.com)** - Not a server", "## Official Servers\n- [Navigation](#top)"]:
            with self.assertRaises(RegistryError):
                _mcpservers_parse(text)

    def test_directory_link_keeps_parentheses_and_multiple_badges(self):
        entries = _mcpservers_parse(
            "## Official Servers\n"
            "- **[Tool Suite](https://example.com/Tools_(Suite))** "
            "![build](https://img.example.com/a) "
            "[![docs](https://img.example.com/b)](https://docs.example.com) "
            "– Practical tools"
        )
        entry, = entries.values()
        self.assertEqual(entry["url"], "https://example.com/Tools_(Suite)")
        self.assertEqual(entry["description"], "Practical tools")

    def test_archive_validation_and_full_notice(self):
        catalog = _mcpservers_archive(archive_bytes())
        self.assertEqual(catalog["attribution"]["licenseText"], LICENSE_TEXT)
        self.assertEqual(catalog["attribution"]["license"], "MIT")
        for raw in [b"not gzip", archive_bytes(license_text="Apache"), archive_bytes(license_text="MIT License\nCopyright (c) 2024 wong2"), archive_bytes(readme="bad"), archive_bytes(readme="x" * (MAX_README_BYTES + 1)), archive_bytes(extras=[("README.md", README)])]:
            with self.subTest(raw_size=len(raw)):
                with self.assertRaises((RegistryError, OSError)):
                    _mcpservers_archive(raw)
        with patch("apps.core.views.labs_mcp_registry.MAX_ARCHIVE_MEMBERS", 1):
            with self.assertRaises(RegistryError):
                _mcpservers_archive(archive_bytes())
        with patch("apps.core.views.labs_mcp_registry.MAX_TAR_BYTES", 100):
            with self.assertRaises(RegistryError):
                _mcpservers_archive(archive_bytes())

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_fetch_limits_timeout_redirects_and_bad_archive(self, get):
        for raw, status in [(b"bad", 200), (b"x" * (MAX_ARCHIVE_BYTES + 1), 200), (b"", 302)]:
            upstream = Mock(status_code=status)
            upstream.iter_content.return_value = [raw]
            get.return_value = upstream
            with self.assertRaises(RegistryError):
                _mcpservers_catalog()
            upstream.close.assert_called_once()
            self.assertIsNone(cache.get(MCPSERVERS_CACHE_KEY))
        get.return_value = directory_response()
        with patch("apps.core.views.labs_mcp_registry.time.monotonic", side_effect=[0, 16]):
            with self.assertRaises(RegistryError):
                _mcpservers_catalog()
        get.side_effect = requests.Timeout()
        with self.assertRaises(RegistryError):
            _mcpservers_catalog()
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(get.call_args.kwargs["timeout"], (3, 10))


def response(payload, status=200):
    result = Mock(status_code=status)
    result.iter_content.return_value = [json.dumps(payload).encode()]
    return result


class RegistryConversionTests(SimpleTestCase):
    def test_remote_rejects_unsupported_url_template(self):
        server = {**SERVER, "remotes": [{"type": "sse", "url": "https://mcp.example.com/{tenant:id}"}]}
        detail = _detail(server)
        self.assertEqual(detail["options"], [])
        self.assertIn("Unsupported remote URL template", detail["unsupportedReason"])

    def test_remote_preview_preserves_template_and_secret_metadata(self):
        option = _detail(SERVER)["options"][0]
        self.assertEqual(option["config"], {
            "type": "http", "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer {smithery_api_key}"},
            "description": "Example tools",
        })
        self.assertEqual(option["fields"], [{
            "key": "smithery_api_key", "label": "smithery_api_key",
            "required": True, "secret": True,
        }])

    def test_sse_and_variable_definitions(self):
        server = copy.deepcopy(SERVER)
        server["remotes"][0]["type"] = "sse"
        server["remotes"][0]["headers"] = [{
            "name": "X-Region", "value": "{region}",
            "variables": {"region": {"default": "west", "description": "Region"}},
        }]
        option = _detail(server)["options"][0]
        self.assertEqual(option["config"]["type"], "sse")
        self.assertEqual(option["fields"][0]["default"], "west")

    def test_fixed_variables_and_container_secrecy(self):
        server = copy.deepcopy(SERVER)
        header = server["remotes"][0]["headers"][0]
        header["variables"] = {"smithery_api_key": {"default": "never expose"}}
        field = _detail(server)["options"][0]["fields"][0]
        self.assertTrue(field["secret"])
        self.assertTrue(field["required"])
        self.assertNotIn("default", field)
        header["variables"] = {"smithery_api_key": {"value": "fixed"}}
        option = _detail(server)["options"][0]
        self.assertEqual(option["fields"], [])
        self.assertEqual(option["config"]["headers"]["Authorization"], "Bearer fixed")

    def test_pinned_npm_env_and_arguments(self):
        server = {**SERVER, "remotes": [], "packages": [{
            "registryType": "npm", "identifier": "@example/tools", "version": "1.2.3",
            "runtimeHint": "npx", "transport": {"type": "stdio"},
            "environmentVariables": [{"name": "API_TOKEN", "isRequired": True, "isSecret": True}],
            "packageArguments": [{"type": "named", "name": "--region", "value": "west"}],
        }]}
        option = _detail(server)["options"][0]
        self.assertEqual(option["config"]["command"], "npx")
        self.assertEqual(option["config"]["args"], ["-y", "@example/tools@1.2.3", "--region", "west"])
        self.assertEqual(option["config"]["env"], {"API_TOKEN": "{env_0}"})
        self.assertTrue(option["fields"][0]["secret"])

    def test_uncertain_packages_are_unsupported_not_guessed(self):
        for package in [
            {"registryType": "pypi", "identifier": "different-dist-name", "runtimeHint": "uvx", "version": "1.0.0"},
            {"registryType": "oci", "identifier": "example/tools", "version": "1.0.0"},
            {"registryType": "npm", "identifier": "example", "version": "latest"},
        ]:
            with self.subTest(package=package):
                detail = _detail({**SERVER, "remotes": [], "packages": [{**package, "transport": {"type": "stdio"}}]})
                self.assertEqual(detail["options"], [])
                self.assertTrue(detail["unsupportedReason"])

    def test_unsafe_urls_are_not_returned(self):
        for url in ["javascript:alert(1)", "file:///etc/passwd", "http://localhost/mcp", "http://127.0.0.1/mcp", "http://169.254.169.254/mcp", "http://[::1]/mcp", "https://user:pass@example.com", "https://{host}/mcp", "https://example.com/%0afoo"]:
            with self.subTest(url=url):
                self.assertEqual(_public_url(url, template=True), "")
                detail = _detail({**SERVER, "websiteUrl": url, "repository": {"url": url}, "remotes": [{"type": "sse", "url": url}]})
                self.assertTrue(detail["url"].startswith(REGISTRY_URL))
                self.assertEqual(detail["options"], [])

    def test_legacy_numeric_ip_aliases_are_not_returned(self):
        for host in [
            "0x7f.0.0.1", "0x7f.1", "0X7F.0x0.0.01.", "0x.0x.0x.0x",
            "127.0x0.0.1", "0177.0.0.1", "127.1", "2130706433",
            "0xa.0.0.1", "0xc0.0250.0.1", "0xac.0x10.0.1",
            "0xa9.0xfe.0xa9.0xfe", "0xa9.254.169.254", "0251.0376.0251.0376",
        ]:
            url = f"http://{host}/mcp"
            with self.subTest(host=host):
                self.assertEqual(_public_url(url, template=True), "")
                detail = _detail({**SERVER, "websiteUrl": url, "repository": {"url": url}, "remotes": [{"type": "sse", "url": url}]})
                self.assertTrue(detail["url"].startswith(REGISTRY_URL))
                self.assertEqual(detail["options"], [])
                with self.assertRaises(RegistryError):
                    _mcpservers_parse("## Official Servers\n- **[Unsafe](" + url + ")** - Unsafe")

    @patch("socket.getaddrinfo", side_effect=AssertionError("URL validation must not resolve DNS"))
    @patch("socket.gethostbyname", side_effect=AssertionError("URL validation must not resolve DNS"))
    @patch("apps.core.views.labs_mcp_registry.requests.get", side_effect=AssertionError("URL validation must not fetch destinations"))
    def test_public_urls_and_non_authority_templates_remain_supported(self, get, gethostbyname, getaddrinfo):
        for url in [
            "https://mcp.example.com/mcp", "https://0x7f.example.com/mcp",
            "https://0x7f.example/mcp", "https://8.8.8.8.example/mcp",
            "https://0127.example.com/mcp", "https://8.8.8.8/mcp",
            "https://[2606:4700:4700::1111]/mcp",
            "https://mcp.example.com/{tenant}/mcp?key={api_key}",
        ]:
            with self.subTest(url=url):
                self.assertEqual(_public_url(url, template=True), url)
                detail = _detail({**SERVER, "remotes": [{"type": "sse", "url": url}]})
                self.assertEqual(detail["options"][0]["config"]["url"], url)

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_fetch_bounds_redirects_json_and_timeout(self, get):
        for upstream in [response({}, 302), response([], 200), Mock(status_code=200)]:
            cache.clear()
            if upstream.status_code == 200 and not isinstance(upstream.iter_content.return_value, list):
                upstream.iter_content.return_value = [b"x" * (MAX_RESPONSE_BYTES + 1)]
            get.return_value = upstream
            with self.assertRaises(RegistryError):
                _fetch()
            upstream.close.assert_called_once()
        cache.clear()
        get.return_value = Mock(status_code=200)
        get.return_value.iter_content.return_value = [b"not json"]
        with self.assertRaises(RegistryError):
            _fetch()
        get.side_effect = requests.Timeout()
        with self.assertRaises(RegistryError):
            _fetch()
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(get.call_args.kwargs["timeout"], (3, 10))


class RegistryApiTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = create_local_user("registry-reader")
        self.space = Space.objects.create(name="Registry", type=2, scope=1, creator=self.user, status=1)
        create_test_space_member(space=self.space, user=self.user, role=2)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.base = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/registry"

    def tearDown(self):
        cache.clear()
        super().tearDown()

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_directory_permissions_and_input_bounds_never_fetch(self, get):
        for endpoint, params in [("search", {"q": "x" * 201}), ("search", {"cursor": "x" * 2049}), ("detail", {"id": "../bad"})]:
            self.assertEqual(self.client.get(self.base + "/" + endpoint, {"source": "mcpservers", **params}).status_code, 400)
        self.assertEqual(self.client.get(self.base + "/search", {"source": "docker"}).status_code, 400)
        for endpoint in ("search", "detail"):
            params = {"source": "mcpservers", "id": "a" * 64}
            self.client.force_authenticate(user=None)
            self.assertIn(self.client.get(self.base + "/" + endpoint, params).status_code, (401, 403))
            self.client.force_authenticate(user=create_local_user("directory-outsider-" + endpoint))
            self.assertEqual(self.client.get(self.base + "/" + endpoint, params).data["code"], 3002)
        get.assert_not_called()

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_directory_search_pagination_cache_and_attribution(self, get):
        readme = "## Official Servers\n" + "\n".join(
            f"- **[Entry {index}](https://github.com/example/project-{index})** - Searchable description"
            for index in range(35)
        )
        get.return_value = directory_response(readme)
        url = self.base + "/search"
        first = self.client.get(url, {"source": "mcpservers"}).data["data"]
        self.assertEqual(len(first["servers"]), 30)
        self.assertEqual(first["attribution"]["licenseText"], LICENSE_TEXT)
        self.assertEqual(first["attribution"]["url"], "https://github.com/wong2/awesome-mcp-servers")
        self.assertEqual(first["attribution"]["directoryUrl"], "https://mcpservers.org/")
        second = self.client.get(url, {"source": "mcpservers", "cursor": first["nextCursor"]}).data["data"]
        self.assertEqual(len(second["servers"]), 5)
        self.assertIsNone(second["nextCursor"])
        self.assertFalse({s["id"] for s in first["servers"]} & {s["id"] for s in second["servers"]})
        for query in ["ENTRY", "SEARCHABLE DESCRIPTION"]:
            self.assertTrue(self.client.get(url, {"source": "mcpservers", "q": query}).data["data"]["servers"])
        self.assertEqual(self.client.get(url, {"source": "mcpservers", "q": "absent"}).data["data"]["servers"], [])
        for cursor, query in [("bad", ""), (first["nextCursor"], "changed"), (first["nextCursor"].split(":")[0] + ":99990", "")]:
            self.assertEqual(self.client.get(url, {"source": "mcpservers", "cursor": cursor, "q": query}).status_code, 400)
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], MCPSERVERS_ARCHIVE_URL)
        cache.clear()
        get.return_value = directory_response(README)
        stale_page = self.client.get(url, {"source": "mcpservers", "cursor": first["nextCursor"]})
        self.assertEqual(stale_page.status_code, 400)
        self.assertEqual(stale_page.data["code"], 3003)
        self.assertEqual(
            stale_page.data["message"],
            "Invalid or stale MCP servers directory cursor. Start a new search.",
        )

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_directory_detail_has_no_configuration(self, get):
        get.return_value = directory_response()
        identifier = next(iter(_mcpservers_parse(README)))
        result = self.client.get(self.base + "/detail", {"source": "mcpservers", "id": identifier})
        self.assertEqual(result.status_code, 200)
        detail = result.data["data"]
        self.assertEqual(detail["options"], [])
        self.assertEqual(detail["unsupportedReason"], "directory_only")
        self.assertNotIn("config", detail)
        self.assertEqual(detail["attribution"]["licenseText"], LICENSE_TEXT)
        self.assertEqual(self.client.get(self.base + "/detail", {"source": "mcpservers", "id": "a" * 64}).status_code, 404)
        self.assertEqual(self.client.get(self.base + "/detail", {"source": "mcpservers", "id": "../bad"}).status_code, 400)
        get.assert_called_once()

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_directory_cache_expiry_is_not_sliding(self, get):
        get.return_value = directory_response()
        for timestamp in [1000, 1599, 1601]:
            with patch("django.core.cache.backends.locmem.time.time", return_value=timestamp):
                self.assertEqual(self.client.get(self.base + "/search", {"source": "mcpservers"}).status_code, 200)
        self.assertEqual(get.call_count, 2)

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_directory_malformed_is_502(self, get):
        get.return_value = response({"broken": True})
        for endpoint, params in [("search", {}), ("detail", {"id": "a" * 64})]:
            result = self.client.get(
                self.base + "/" + endpoint, {"source": "mcpservers", **params}
            )
            self.assertEqual(result.status_code, 502)
            self.assertEqual(result.data["code"], 3030)
            self.assertEqual(
                result.data["message"],
                "The MCP servers directory could not be loaded. Try again later.",
            )

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_corrupt_deflate_is_502_without_cache(self, get):
        upstream = Mock(status_code=200)
        upstream.iter_content.return_value = [b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\x07"]
        get.return_value = upstream
        result = self.client.get(self.base + "/search", {"source": "mcpservers"})
        self.assertEqual(result.status_code, 502)
        self.assertIsNone(cache.get(MCPSERVERS_CACHE_KEY))

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_search_pagination_and_short_cache(self, get):
        get.return_value = response({"servers": [{"server": SERVER}], "metadata": {"nextCursor": "page2"}})
        for _ in range(2):
            result = self.client.get(self.base + "/search", {"source": "official", "q": "tools", "cursor": "page1"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.data["code"], 0)
            self.assertEqual(result.data["data"]["nextCursor"], "page2")
            self.assertEqual(result.data["data"]["servers"][0]["source"], "official")
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], REGISTRY_URL)
        self.assertEqual(get.call_args.kwargs["params"], {"search": "tools", "cursor": "page1", "limit": 30, "version": "latest"})

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_cache_hits_do_not_extend_expiry(self, get):
        get.return_value = response({"servers": [{"server": SERVER}], "metadata": {}})
        with patch("django.core.cache.backends.locmem.time.time", return_value=1000):
            self.assertEqual(self.client.get(self.base + "/search").status_code, 200)
        with patch("django.core.cache.backends.locmem.time.time", return_value=1050):
            self.assertEqual(self.client.get(self.base + "/search").status_code, 200)
        with patch("django.core.cache.backends.locmem.time.time", return_value=1061):
            self.assertEqual(self.client.get(self.base + "/search").status_code, 200)
        self.assertEqual(get.call_count, 2)

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_detail_is_encoded_and_read_only(self, get):
        get.return_value = response({"server": SERVER})
        result = self.client.get(self.base + "/detail", {"id": SERVER["name"], "source": "official"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["data"], _detail(SERVER))
        self.assertEqual(get.call_args.args[0], REGISTRY_URL + "/io.github.example%2Ftools/versions/latest")

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_malformed_upstream_is_502_not_empty(self, get):
        for payload in [{}, {"servers": [], "metadata": []}, {"servers": [{}], "metadata": {}}, {"servers": [], "metadata": {"nextCursor": 1}}]:
            get.return_value = response(payload)
            result = self.client.get(self.base + "/search")
            self.assertEqual(result.status_code, 502)
            self.assertEqual(result.data["code"], 3030)
            self.assertEqual(
                result.data["message"],
                "The official MCP registry could not be loaded. Try again later.",
            )
        get.return_value = response({"server": {**SERVER, "name": "other/name"}})
        result = self.client.get(self.base + "/detail", {"id": SERVER["name"]})
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.data["code"], 3030)
        self.assertEqual(
            result.data["message"],
            "The official MCP registry could not be loaded. Try again later.",
        )
        get.return_value = response({"servers": [], "metadata": {}})
        self.assertEqual(self.client.get(self.base + "/search").data["data"], {"servers": [], "nextCursor": None})

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_invalid_parameters_never_fetch(self, get):
        for endpoint, params in [("search", {"source": "other"}), ("search", {"q": "a" * 201}), ("search", {"cursor": "a" * 2049}), ("detail", {"id": "https://evil.example/path"}), ("detail", {"id": "../secret"}), ("detail", {})]:
            self.assertEqual(self.client.get(self.base + "/" + endpoint, params).status_code, 400)
        get.assert_not_called()

    @patch("apps.core.views.labs_mcp_registry.requests.get")
    def test_access_failures_never_fetch(self, get):
        for endpoint in ("search", "detail"):
            url = self.base + "/" + endpoint
            self.client.force_authenticate(user=None)
            self.assertIn(self.client.get(url).status_code, (401, 403))
            self.client.force_authenticate(user=create_local_user("outsider-" + endpoint))
            denied = self.client.get(url)
            self.assertEqual(denied.status_code, 400)  # Existing space-access envelope.
            self.assertEqual(denied.data["code"], 3002)
            self.client.force_authenticate(user=self.user)
            self.space.status = 0
            self.space.save(update_fields=["status"])
            self.assertEqual(self.client.get(url).status_code, 404)
            self.space.status = 1
            self.space.save(update_fields=["status"])
        get.assert_not_called()
