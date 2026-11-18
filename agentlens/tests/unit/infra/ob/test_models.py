from sqlalchemy import inspect

from agentlens.infra.ob.engine import make_engine
from agentlens.infra.ob.models import Base


def test_create_all_produces_expected_tables():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    names = set(inspect(eng).get_table_names())
    assert names == {
        "agentlens_diagnoser",
        "agentlens_model_connection",
        "agentlens_dataset",
        "agentlens_job",
        "agentlens_task",
        "agentlens_heartbeat",
        "agentlens_leader_lease",
        "agentlens_sandbox_connection",
        "agentlens_sandbox_binding",
        "agentlens_sandbox_lease",
    }


def test_diagnoser_columns():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    columns = inspect(eng).get_columns("agentlens_diagnoser")
    cols = {column["name"] for column in columns}
    assert cols >= {
        "id",
        "uuid",
        "space_uuid",
        "name",
        "description",
        "type",
        "mode",
        "spec",
        "version",
        "gmt_create",
        "gmt_modified",
        "creator",
        "modifier",
    }
    agent_uuid = next(column for column in columns if column["name"] == "agent_uuid")
    assert agent_uuid["nullable"] is False
    assert agent_uuid["default"] is None


def test_task_has_attempt_and_no_trigger_type():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("agentlens_task")}
    assert "attempt" in cols
    assert "trigger_type" not in cols
    assert "worker" in cols


def test_job_spec_maps_to_config_column():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("agentlens_job")}
    assert "config" in cols
    assert "spec" not in cols


def test_heartbeat_has_no_audit_user_columns():
    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("agentlens_heartbeat")}
    assert "creator" not in cols
    assert "modifier" not in cols
