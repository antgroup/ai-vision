import json
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL

from agentlens.diagnoser.base import Executor  # noqa: E402
from agentlens.diagnoser.registry import ExecutorRegistry  # noqa: E402
from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric  # noqa: E402
from agentlens.domain.enums import Verdict  # noqa: E402
from agentlens.infra.ob.engine import make_engine, make_session_factory  # noqa: E402
from agentlens.infra.ob.models import Base  # noqa: E402


@ExecutorRegistry.register("rule", "test_fixed")
class _FixedRuleExecutor(Executor):
    """Deterministic custom executor used only by integration-style tests."""

    def evaluate(self, trace) -> DiagnosisResult:
        metrics = self.spec.get("metrics") or []
        if not metrics:
            raise AssertionError("test_fixed requires a metric object")
        metric_name = str(metrics[0].get("name") or "quality")
        return DiagnosisResult(
            metrics=[Metric(name=metric_name, verdict=Verdict.GOOD)],
            execution=Execution.succeeded(),
        )


@pytest.fixture(scope="session")
def mysql_url():
    # No implicit container or production DB: both supported engines are explicit,
    # disposable loopback databases created by the current test run.
    config_file = os.environ.get("AGENTLENS_TEST_DB_CONFIG_FILE")
    backend = os.environ.get("AGENTLENS_TEST_DB_BACKEND")
    if not config_file:
        pytest.skip("DB tests require an explicit isolated OceanBase/seekdb config file")
    if backend not in {"oceanbase", "seekdb"}:
        raise ValueError("AGENTLENS_TEST_DB_BACKEND must be oceanbase or seekdb")
    config = json.loads(Path(config_file).read_text())
    if set(config) != {"host", "port", "user", "password", "database"}:
        raise ValueError("test DB config has unexpected fields")
    if config["host"] not in {"127.0.0.1", "localhost", "::1"} or not re.fullmatch(
        r"aiv_diag_unit_[a-zA-Z0-9_]+", config["database"]
    ):
        raise ValueError("test DB must be a disposable aiv_diag_unit_ database on loopback")
    yield URL.create(
        "mysql+pymysql",
        username=config["user"],
        password=config["password"],
        host=config["host"],
        port=int(config["port"]),
        database=config["database"],
    ).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def engine(mysql_url):
    eng = make_engine(mysql_url)
    existing = set(inspect(eng).get_table_names())
    if existing:
        eng.dispose()
        raise ValueError("test DB must be empty; refusing to modify existing tables")
    Base.metadata.create_all(eng)
    # seed leader_lease 行（迁移做的事，单测里手动 mock）
    with eng.begin() as c:
        c.execute(
            text("""
            INSERT INTO agentlens_leader_lease
              (uuid, lock_key, holder_uuid, acquired_at, expires_at,
               fenced_token, gmt_create, gmt_modified)
            VALUES ('lease-master-seed', 'master', '__bootstrap__',
                    NOW(), NOW(), 0, NOW(), NOW())
        """)
        )
    try:
        yield eng
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()


@pytest.fixture
def session(engine):
    sf = make_session_factory(engine)
    s = sf()
    try:
        yield s
    finally:
        s.rollback()
        # 每个测试清表（顺序：先依赖方再被依赖方）
        s.execute(text("DELETE FROM agentlens_task"))
        s.execute(text("DELETE FROM agentlens_heartbeat"))
        s.execute(text("DELETE FROM agentlens_job"))
        s.execute(text("DELETE FROM agentlens_model_connection"))
        s.execute(text("DELETE FROM agentlens_diagnoser"))
        s.execute(text("DELETE FROM agentlens_dataset"))
        # leader_lease 保留 seed 行
        s.execute(
            text("UPDATE agentlens_leader_lease SET holder_uuid='__bootstrap__', fenced_token=0")
        )
        s.commit()
        s.close()


from fastapi.testclient import TestClient  # noqa: E402

from agentlens.infra.antcollector import InMemoryAntcollectorClient  # noqa: E402
from agentlens.infra.signoz.client import InMemorySignozClient  # noqa: E402
from agentlens.infra.signoz.dialect import FlatColumnDialect  # noqa: E402
from agentlens.main import build_app  # noqa: E402
from agentlens.service.datasource.signoz import TraceStore  # noqa: E402
from agentlens.service.diagnosis_record_store import DiagnosisRecordStore  # noqa: E402
from agentlens.service.task_stats_store import TaskStatsStore  # noqa: E402
from agentlens.web import deps  # noqa: E402


@pytest.fixture
def signoz_inmem():
    return InMemorySignozClient()


@pytest.fixture
def api_token():
    return "test-token"


@pytest.fixture
def app(monkeypatch, mysql_url, signoz_inmem, api_token, session):
    monkeypatch.setenv("AGENTLENS_OB_URL", mysql_url)
    monkeypatch.setenv("AGENTLENS_TOKEN", api_token)
    monkeypatch.setenv("AGENTLENS_HOSTNAME", "test-host")
    monkeypatch.setenv("AGENTLENS_IP", "127.0.0.1")
    monkeypatch.setenv("SIGNOZ_OB_URL", "mysql+pymysql://in-mem")
    monkeypatch.setenv("SIGNOZ_ORG_ID", "test-org")
    monkeypatch.setenv("SIGNOZ_SPANS_TABLE", "test-ns_trace")
    monkeypatch.setenv("ANTCOLLECTOR_ENDPOINT", "http://mock/v1/traces")
    # 关闭首访自动补齐内置 Rule，避免列表类断言被默认诊断器污染；
    # 该行为由 test_diagnoser_routes 中的专项用例显式开启验证。
    monkeypatch.setenv("AGENTLENS_DEFAULT_BUILTIN_SCENARIOS", "")
    deps.get_settings.cache_clear()
    deps._engine_factory.cache_clear()
    deps.get_signoz_client.cache_clear()
    deps.get_column_dialect.cache_clear()
    deps.get_trace_store.cache_clear()

    a = build_app()

    def _get_session_override():
        yield session

    a.dependency_overrides[deps.get_session] = _get_session_override
    antcollector_inmem = InMemoryAntcollectorClient(signoz_inmem._tables)
    a.dependency_overrides[deps.get_signoz_client] = lambda: signoz_inmem
    a.dependency_overrides[deps.get_column_dialect] = FlatColumnDialect
    a.dependency_overrides[deps.get_antcollector_client] = lambda: antcollector_inmem
    a.dependency_overrides[deps.get_trace_store] = lambda: TraceStore(signoz_inmem)
    # Mirror deps.get_record_store: use the same table/db the runner writes to
    # (settings.diagnosis_record_table), so list-records reads the written rows.
    _settings = deps.get_settings()
    a.dependency_overrides[deps.get_record_store] = lambda: DiagnosisRecordStore(
        signoz_inmem,
        antcollector_inmem,
        table=_settings.diagnosis_record_table,
    )
    a.dependency_overrides[deps.get_task_stats_store] = lambda: TaskStatsStore(
        signoz_inmem,
        antcollector_inmem,
        table=_settings.diagnosis_record_table,
    )
    return a


@pytest.fixture
def client(app, api_token):
    c = TestClient(app)
    c.headers.update({"x-antmonitor-token": api_token})
    return c
