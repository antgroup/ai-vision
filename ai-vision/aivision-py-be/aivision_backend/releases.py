"""Read public release notes without sending any deployment information."""

import http.client
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

PRODUCT_REPOSITORY = "antgroup/ai-vision"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{PRODUCT_REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{PRODUCT_REPOSITORY}/releases/tag/"
CACHE_KEY = f"aivision:latest-release:{PRODUCT_REPOSITORY}:v1"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_NOTES_BYTES = 64 * 1024
RELEASE_TAG = re.compile(r"v?((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))")
PUBLISHED_AT = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _published_release(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid release")
    tag = value.get("tag_name")
    match = (
        RELEASE_TAG.fullmatch(tag) if isinstance(tag, str) and len(tag) <= 64 else None
    )
    published_at = value.get("published_at")
    if (
        not match
        or value.get("draft") is not False
        or value.get("prerelease") is not False
        or not isinstance(published_at, str)
        or not PUBLISHED_AT.fullmatch(published_at)
    ):
        raise ValueError("Not a published product release")
    datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    name = value.get("name") or tag
    body = value.get("body") if value.get("body") is not None else ""
    if (
        not isinstance(name, str)
        or len(name) > 256
        or not isinstance(body, str)
        or len(body.encode("utf-8")) > MAX_NOTES_BYTES
    ):
        raise ValueError("Invalid release notes")
    return {
        "version": match.group(1),
        "tag_name": tag,
        "name": name,
        "body": body,
        "url": RELEASES_URL + tag,
        "published_at": published_at,
    }


def _check_release():
    result = {"status": "unavailable", "latest": None}
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AI-Vision-Update-Check",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(
            request, timeout=3
        ) as response:
            if response.status != 200:
                raise ValueError("Unexpected release response")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Release response too large")
        result["latest"] = _published_release(json.loads(raw.decode("utf-8")))
        result["status"] = "available"
    except urllib.error.HTTPError as error:
        if error.code == 404:
            result["status"] = "no_release"
        error.close()
    except (
        OSError,
        http.client.HTTPException,
        ValueError,
        UnicodeError,
        RecursionError,
    ):
        pass
    result["checked_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return result


@csrf_exempt
@never_cache
@require_GET
def release_info(request):
    """A cached update reference; checking does not install or verify an upgrade."""
    enabled = os.environ.get("AIVISION_UPDATE_CHECK_ENABLED", "true").strip().lower()
    if enabled in {"false", "0", "off", "no"}:
        data = {"status": "disabled", "latest": None, "checked_at": None}
    else:
        data = cache.get(CACHE_KEY)
        if data is None:
            data = _check_release()
            cache.set(CACHE_KEY, data, 60 if data["status"] == "unavailable" else 300)
    return JsonResponse({"code": 0, "message": "success", "data": data})
