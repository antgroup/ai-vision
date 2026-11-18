"""SigNoz integration bootstrap for the AI Vision control plane.

The local forge stack starts signoz-ob UNREGISTERED (``SIGNOZ_USER_ROOT_ENABLED
=false``), so the deployment's single SigNoz org is *system-produced* here rather
than hardcoded anywhere. This module is the Python port of the CLI's former
``internal/auth`` token flow:

    wait ready -> register root (=> orgId) -> checkpoint org -> login -> resolve role ->
    ensure service account -> bind role -> mint internal API key ->
    configure the signoz DataSource routes -> mark integration complete

The minted ``SIGNOZ-API-KEY`` is a deployment-internal credential: it is written
only into the signoz ``DataSource`` config (the same place the rest of the signoz
route config lives) and is never echoed by any status command, log line or API
response. Only the non-sensitive ``organization_id`` is persisted for reuse and
exposed (so ingest tokens can stamp it and cast can discover it).

Wire contract mirrors the public SigNoz HTTP API:
  * ``GET  /api/v1/health``                       readiness probe
  * ``GET  /api/v1/version``                      ``data.setupCompleted``
  * ``POST /api/v1/register``                     ``{name,email,password,orgDisplayName,orgName}`` -> ``data.orgId``
  * ``POST /api/v2/sessions/email_password``      ``{email,password,orgId}`` -> ``data.accessToken``
  * ``GET  /api/v1/roles``                        ``data[].{id,name}``
  * ``GET|POST /api/v1/service_accounts``         ``data[].{id,name}``
  * ``POST /api/v1/service_account_roles``        ``{serviceAccountId,roleId}``
  * ``POST /api/v1/service_accounts/{id}/keys``   ``{name,expiresAt:0}`` -> ``data.key``
"""

from __future__ import annotations

import io
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests
from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import transaction
from django.utils import timezone

from apps.core.models import (
    AgentTypeConfig,
    ObservationEndpointConfig,
    PlatformConfig,
)

logger = logging.getLogger(__name__)

# PlatformConfig singleton row that records the produced integration state.
PLATFORM_CODE = "signoz_integration"
PLATFORM_NAME = "SigNoz Integration"

# Internal service account the backend uses to proxy user queries to signoz.
SERVICE_ACCOUNT_NAME = "aivision-backend"
# signoz-admin is the managed admin role; the key stays inside the backend.
DEFAULT_ROLE_NAME = "signoz-admin"

DEFAULT_READY_TIMEOUT = 600  # seconds to wait for signoz to become reachable
DEFAULT_POLL_INTERVAL = 2  # seconds between readiness probes
REQUEST_TIMEOUT = 30  # per-request HTTP timeout


class SigNozIntegrationError(Exception):
    """Raised when the SigNoz bootstrap cannot be completed."""


@dataclass
class BootstrapResult:
    endpoint: str = ""
    query_endpoint: str = ""
    organization_id: str = ""
    service_account_id: str = ""
    role_id: str = ""
    registered: bool = False  # True when this run registered the root user
    reused: bool = False  # True when the fast-path skipped an existing config
    minted_key: bool = False
    agent_products: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SigNoz response envelope helpers ({"status":"success","data": ...})
# ---------------------------------------------------------------------------
def _data(body: dict[str, Any]) -> dict[str, Any]:
    d = body.get("data")
    return d if isinstance(d, dict) else body


def _data_list(body: dict[str, Any]) -> list[dict[str, Any]]:
    d = body.get("data")
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)]
    return []


def _field(body: dict[str, Any], key: str) -> str:
    """Pull a scalar field out of ``data`` (falling back to the top level)."""
    for src in (_data(body), body):
        if not isinstance(src, dict):
            continue
        v = src.get(key)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return ""


def _snippet(text: str, limit: int = 200) -> str:
    s = (text or "").strip()
    if len(s) > limit:
        s = s[:limit] + "…"
    return s or "(empty body)"


# ---------------------------------------------------------------------------
# Minimal SigNoz HTTP client (stdlib-shaped, mirrors the Go reference flow)
# ---------------------------------------------------------------------------
class _Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self._session = requests.Session()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        bearer: str | None = None,
    ) -> dict[str, Any]:
        url = self.base + path
        headers = {"Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        try:
            resp = self._session.request(
                method, url, json=json_body, headers=headers, timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            raise SigNozIntegrationError(f"{method} {url} failed: {exc}") from exc
        if not 200 <= resp.status_code < 300:
            raise SigNozIntegrationError(
                f"{method} {url} -> {resp.status_code}: {_snippet(resp.text)}"
            )
        text = (resp.text or "").strip()
        if not text:
            return {}
        try:
            body = resp.json()
        except ValueError as exc:
            raise SigNozIntegrationError(
                f"decode response from {url}: {exc}"
            ) from exc
        return body if isinstance(body, dict) else {"data": body}

    def probe(self, path: str) -> int:
        try:
            resp = self._session.get(self.base + path, timeout=REQUEST_TIMEOUT)
            return resp.status_code
        except requests.RequestException:
            return 0


# ---------------------------------------------------------------------------
# Flow steps
# ---------------------------------------------------------------------------
def _wait_ready(client: _Client, timeout: int, interval: int, log: Callable) -> None:
    deadline = time.monotonic() + max(1, timeout)
    while True:
        if 200 <= client.probe("/api/v1/health") < 300:
            return
        if time.monotonic() >= deadline:
            raise SigNozIntegrationError(
                f"timed out after {timeout}s waiting for {client.base}/api/v1/health"
            )
        time.sleep(max(1, interval))


def _setup_completed(client: _Client) -> bool:
    body = client.request("GET", "/api/v1/version")
    for src in (_data(body), body):
        if isinstance(src, dict):
            v = src.get("setupCompleted")
            if isinstance(v, bool):
                return v
    return False


def _register(client: _Client, email: str, password: str, org_name: str) -> str:
    body = client.request(
        "POST",
        "/api/v1/register",
        json_body={
            "name": "Admin",
            "email": email,
            "password": password,
            "orgDisplayName": org_name,
            "orgName": "signoz",
        },
    )
    org = _field(body, "orgId")
    if not org:
        raise SigNozIntegrationError("register succeeded but returned no orgId")
    return org


def _login(client: _Client, email: str, password: str, org_id: str) -> str:
    body = client.request(
        "POST",
        "/api/v2/sessions/email_password",
        json_body={"email": email, "password": password, "orgId": org_id},
    )
    token = _field(body, "accessToken")
    if not token:
        raise SigNozIntegrationError("login returned no accessToken")
    return token


def _find_role(client: _Client, jwt: str, role_name: str) -> str:
    body = client.request("GET", "/api/v1/roles", bearer=jwt)
    for item in _data_list(body):
        if item.get("name") == role_name:
            rid = item.get("id")
            if isinstance(rid, str) and rid:
                return rid
    raise SigNozIntegrationError(f"role {role_name!r} not found")


def _ensure_service_account(client: _Client, jwt: str, name: str) -> str:
    try:
        body = client.request("GET", "/api/v1/service_accounts", bearer=jwt)
        for item in _data_list(body):
            if item.get("name") == name:
                sid = item.get("id")
                if isinstance(sid, str) and sid:
                    return sid
    except SigNozIntegrationError:
        # Listing may be unavailable; fall through to create.
        pass
    created = client.request(
        "POST", "/api/v1/service_accounts", json_body={"name": name}, bearer=jwt
    )
    sid = _field(created, "id")
    if not sid:
        raise SigNozIntegrationError("service account created but no id returned")
    return sid


def _bind_role(client: _Client, jwt: str, sa_id: str, role_id: str) -> None:
    client.request(
        "POST",
        "/api/v1/service_account_roles",
        json_body={"serviceAccountId": sa_id, "roleId": role_id},
        bearer=jwt,
    )


def _mint_key(client: _Client, jwt: str, sa_id: str) -> str:
    body = client.request(
        "POST",
        f"/api/v1/service_accounts/{sa_id}/keys",
        json_body={
            "name": f"{SERVICE_ACCOUNT_NAME}-key-{int(time.time())}",
            "expiresAt": 0,  # never expires
        },
        bearer=jwt,
    )
    key = _field(body, "key")
    if not key:
        raise SigNozIntegrationError("mint key returned no key")
    return key


# ---------------------------------------------------------------------------
# Persistence + org resolution
# ---------------------------------------------------------------------------
def _recover_org_from_metastore(
    endpoint: str, email: str, log: Callable = logger.info
) -> str:
    """Best-effort compatibility recovery for host processes with Docker access.

    A retained SigNoz sqlite metastore can outlive the backend integration row.
    The source deployment may run this backend on the host with Docker available;
    keep its legacy recovery path. Container deployments do not require Docker
    access and normally resume from the organization checkpoint below.
    """
    import subprocess
    from urllib.parse import urlparse

    host = urlparse(endpoint).hostname or "signoz-ob"
    sqlite_path = "/var/lib/signoz/signoz.db"
    query = "SELECT org_id FROM users WHERE email = ? LIMIT 1"
    try:
        result = subprocess.run(
            ["docker", "exec", host, "python3", "-c",
             f"import sqlite3; "
             f"conn = sqlite3.connect({sqlite_path!r}); "
             f"cur = conn.cursor(); "
             f"cur.execute({query!r}, ({email!r},)); "
             f"row = cur.fetchone(); "
             f"print(row[0] if row else '')"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log("could not reach signoz container to recover org: %s", exc)
        return ""
    org = (result.stdout or "").strip()
    if org:
        log("recovered org %s from signoz metastore", org)
    return org


def stored_state() -> dict[str, Any]:
    """The persisted integration state ({} when never bootstrapped)."""
    pc = PlatformConfig.objects.filter(platform_code=PLATFORM_CODE).first()
    if pc is not None and isinstance(pc.config, dict):
        return dict(pc.config)
    return {}


def stored_organization_id() -> str:
    return str(stored_state().get("organization_id") or "").strip()


def current_ingest_org_id() -> str:
    """Org id that ingest tokens stamp.

    An explicit ``AIVISION_INGEST_ORG_ID`` env override wins (for advanced or
    pre-provisioned deployments); otherwise the system-produced org persisted by
    :func:`bootstrap` is used. Returns "" when neither is available, in which
    case the token simply omits the org claim.
    """
    configured = (getattr(settings, "AIVISION_INGEST_ORG_ID", "") or "").strip()
    if configured:
        return configured
    return stored_organization_id()


def _save_state(
    *,
    endpoint: str,
    query_endpoint: str,
    org_id: str,
    sa_id: str,
    role_id: str,
    registered: bool,
    products: list[str],
    configured: bool = True,
) -> None:
    config = {
        "organization_id": org_id,
        "endpoint": endpoint,
        "query_endpoint": query_endpoint,
        "service_account_id": sa_id,
        "role_id": role_id,
        "registered": registered,
        "configured": configured,
        "agent_products": list(products),
        "bootstrapped_at": timezone.now().isoformat() if configured else "",
    }
    PlatformConfig.objects.update_or_create(
        platform_code=PLATFORM_CODE,
        defaults={
            "platform_name": PLATFORM_NAME,
            "api_endpoint": query_endpoint,
            "config": config,
            "status": 1,
        },
    )


def _stamp_default_observation_endpoint(org_id: str) -> bool:
    """Record the org on the default observation endpoint config, when present.

    ``space_observation_provisioning`` reads ``config['organization_id']`` from
    the default :class:`ObservationEndpointConfig`; stamping it keeps that path
    consistent with the produced org. A stack without such a row is fine (the
    PlatformConfig store above remains the authoritative org source).
    """
    ep = (
        ObservationEndpointConfig.objects.filter(gmt_deleted__isnull=True)
        .order_by("-is_default", "-gmt_create")
        .first()
    )
    if ep is None:
        return False
    config = dict(ep.config or {})
    if str(config.get("organization_id") or "") == org_id:
        return False
    config["organization_id"] = org_id
    ep.config = config
    ep.save(update_fields=["config", "gmt_modified"])
    return True


# ---------------------------------------------------------------------------
# DataSource route configuration (reuses the existing management command)
# ---------------------------------------------------------------------------
def _active_agent_products() -> list[str]:
    return list(
        AgentTypeConfig.objects.filter(status=1, gmt_deleted__isnull=True)
        .exclude(agent_product="")
        .values_list("agent_product", flat=True)
        .distinct()
    )


def _configure_datasources(
    query_endpoint: str, api_key: str, products: list[str], make_default: bool,
    log: Callable,
) -> int:
    """Point every active agent product's signoz routes at the query endpoint.

    Reuses ``configure_signoz_datasource`` (which reads the key from the
    ``SIGNOZ_API_KEY`` env var) so route shape stays identical to the rest of the
    codebase. The key is set only for the duration of these calls.
    """
    if not products:
        return 0
    prev = os.environ.get("SIGNOZ_API_KEY")
    os.environ["SIGNOZ_API_KEY"] = api_key
    configured = 0
    try:
        for product in products:
            buf = io.StringIO()
            try:
                call_command(
                    "configure_signoz_datasource",
                    endpoint=query_endpoint,
                    agent_product=product,
                    make_default=make_default,
                    stdout=buf,
                )
                configured += 1
            except CommandError as exc:
                # One mis-shaped product must not abort the whole bootstrap.
                log("warning: signoz routes for %r not configured: %s", product, exc)
    finally:
        if prev is None:
            os.environ.pop("SIGNOZ_API_KEY", None)
        else:
            os.environ["SIGNOZ_API_KEY"] = prev
    return configured


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def bootstrap(
    *,
    endpoint: str | None = None,
    query_endpoint: str | None = None,
    email: str | None = None,
    password: str | None = None,
    org_name: str | None = None,
    org_id: str | None = None,
    role_name: str = DEFAULT_ROLE_NAME,
    make_default: bool = True,
    force: bool = False,
    ready_timeout: int = DEFAULT_READY_TIMEOUT,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    log: Callable = logger.info,
) -> BootstrapResult:
    """Run the idempotent SigNoz integration bootstrap and persist the org.

    Never logs or returns the minted API key.
    """
    endpoint = (endpoint or getattr(settings, "SIGNOZ_INTERNAL_URL", "") or "").strip()
    if not endpoint:
        raise SigNozIntegrationError("SIGNOZ_INTERNAL_URL is not configured")
    email = (email or getattr(settings, "SIGNOZ_ROOT_EMAIL", "") or "").strip()
    password = password or getattr(settings, "SIGNOZ_USER_ROOT_PASSWORD", "") or ""
    org_name = (
        org_name or getattr(settings, "SIGNOZ_ROOT_ORG_NAME", "") or "AI Vision"
    ).strip()
    if not email or not password:
        raise SigNozIntegrationError(
            "SIGNOZ_ROOT_EMAIL and SIGNOZ_USER_ROOT_PASSWORD are required"
        )
    query_endpoint = (
        query_endpoint
        or getattr(settings, "AIVISION_SIGNOZ_QUERY_ENDPOINT", "")
        or endpoint
    ).strip().rstrip("/")

    existing = stored_state()

    # Idempotent fast-path: already bootstrapped and not forced -> reuse. The
    # minted key already lives in the DataSource routes, so nothing to redo.
    if existing.get("configured") and existing.get("organization_id") and not force:
        log(
            "SigNoz integration already configured (org=%s); use --force to redo",
            existing["organization_id"],
        )
        return BootstrapResult(
            endpoint=endpoint,
            query_endpoint=existing.get("query_endpoint", query_endpoint),
            organization_id=str(existing["organization_id"]),
            service_account_id=existing.get("service_account_id", ""),
            role_id=existing.get("role_id", ""),
            reused=True,
            agent_products=list(existing.get("agent_products", [])),
        )

    client = _Client(endpoint)
    log("waiting for %s to become ready…", endpoint)
    _wait_ready(client, ready_timeout, poll_interval, log)

    setup_done = _setup_completed(client)
    registered = False
    resolved_org = (org_id or "").strip()
    if not setup_done:
        log("registering root user %s…", email)
        resolved_org = resolved_org or _register(client, email, password, org_name)
        registered = True
    else:
        log("deployment already registered; signing in…")
    if not resolved_org:
        resolved_org = str(existing.get("organization_id") or "").strip()
    if not resolved_org:
        resolved_org = _recover_org_from_metastore(endpoint, email, log)
    if not resolved_org:
        raise SigNozIntegrationError(
            "could not determine orgId; pass --org-id for an existing deployment "
            "whose backend integration state is missing"
        )

    # Registration changes SigNoz before the remaining network/database work.
    # Persist its non-sensitive identity immediately so a failure in login,
    # key creation or route configuration can resume without registering again.
    # `configured` remains false until all routes and observation state commit.
    # The backend does not require Docker access to recover this checkpoint.
    registered_here = registered or bool(
        existing.get("registered") and existing.get("organization_id") == resolved_org
    )
    _save_state(
        endpoint=endpoint,
        query_endpoint=query_endpoint,
        org_id=resolved_org,
        sa_id="",
        role_id="",
        registered=registered_here,
        products=[],
        configured=False,
    )

    jwt = _login(client, email, password, resolved_org)
    log("signed in as %s", email)
    role_id = _find_role(client, jwt, role_name)
    sa_id = _ensure_service_account(client, jwt, SERVICE_ACCOUNT_NAME)
    try:
        _bind_role(client, jwt, sa_id, role_id)
    except SigNozIntegrationError as exc:
        log("warning: could not bind role to service account: %s", exc)
    api_key = _mint_key(client, jwt, sa_id)
    log("minted internal API key for service account %r", SERVICE_ACCOUNT_NAME)

    products = _active_agent_products()
    configured = _configure_datasources(
        query_endpoint, api_key, products, make_default, log
    )

    with transaction.atomic():
        _save_state(
            endpoint=endpoint,
            query_endpoint=query_endpoint,
            org_id=resolved_org,
            sa_id=sa_id,
            role_id=role_id,
            registered=registered_here,
            products=products,
        )
        _stamp_default_observation_endpoint(resolved_org)

    log(
        "configured signoz routes for %d/%d agent product(s)", configured, len(products)
    )
    return BootstrapResult(
        endpoint=endpoint,
        query_endpoint=query_endpoint,
        organization_id=resolved_org,
        service_account_id=sa_id,
        role_id=role_id,
        registered=registered,
        minted_key=True,
        agent_products=products,
    )
