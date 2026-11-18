import pytest
from sqlalchemy.exc import IntegrityError

from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.enums import DiagnoserMode, DiagnoserType
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.utils.uuid_gen import new_diagnoser_uuid


def _make(name="d", space="s", agent="", spec=None, type=DiagnoserType.BUILTIN) -> Diagnoser:
    return Diagnoser(
        uuid=new_diagnoser_uuid(),
        space_uuid=space,
        agent_uuid=agent,
        name=name,
        description="",
        type=type,
        mode=DiagnoserMode.RULE,
        spec=spec or {"rule": "x"},
        version="v1",
        gmt_create=None,
        gmt_modified=None,
        creator="u",
        modifier="u",
    )


def test_insert_then_get_by_uuid(session):
    t = DiagnoserTable(session)
    d = _make(agent="ag-1")
    t.insert(d)
    fetched = t.get_by_uuid(d.uuid)
    assert fetched is not None
    assert fetched.uuid == d.uuid
    assert fetched.name == "d"
    assert fetched.agent_uuid == "ag-1"  # agent 维度 round-trip
    assert fetched.type == DiagnoserType.BUILTIN


def test_same_name_different_agent_coexist(session):
    """uk=(space,agent,name)：同 space 同名、不同 agent 可共存；同三元组才冲突。"""
    t = DiagnoserTable(session)
    t.insert(_make(name="x", space="s", agent="a1"))
    t.insert(_make(name="x", space="s", agent="a2"))  # 不同 agent，放行
    with pytest.raises(IntegrityError):
        t.insert(_make(name="x", space="s", agent="a1"))  # 同三元组，冲突


def test_get_by_uuid_returns_none_for_missing(session):
    assert DiagnoserTable(session).get_by_uuid("dg-deadbeef") is None


def test_duplicate_name_in_same_space_raises(session):
    t = DiagnoserTable(session)
    t.insert(_make(name="x"))
    with pytest.raises(IntegrityError):
        t.insert(_make(name="x"))


def test_list_returns_exact_scope(session):
    t = DiagnoserTable(session)
    t.insert(_make(name="exact", space="s1", agent="agent-1"))
    t.insert(_make(name="other-space", space="s2", agent="agent-1"))
    t.insert(_make(name="other-agent", space="s1", agent="agent-2"))

    rows = t.list(space_uuid="s1", agent_uuid="agent-1")

    assert [row.name for row in rows] == ["exact"]


def test_custom_diagnoser_is_agent_scoped(session):
    t = DiagnoserTable(session)
    custom = _make(
        name="agent-custom",
        space="s1",
        agent="agent-a",
        type=DiagnoserType.CUSTOM,
    )
    t.insert(custom)

    fetched = t.get_by_uuid(custom.uuid)
    assert fetched is not None
    assert fetched.agent_uuid == "agent-a"
    rows = t.list(space_uuid="s1", agent_uuid="agent-b")
    assert rows == []


def test_list_selected_spaces_keeps_exact_agent_and_order(session):
    t = DiagnoserTable(session)
    t.insert(_make(name="exact", space="s1", agent="agent-1"))
    t.insert(_make(name="user", space="s-user_shared", agent="agent-1"))
    t.insert(_make(name="biz", space="s-biz_shared", agent="agent-1"))
    t.insert(_make(name="shared", space="s-shard", agent="agent-1"))
    t.insert(_make(name="same-name", space="s1", agent="agent-1"))
    t.insert(_make(name="same-name", space="s-shard", agent="agent-1"))
    t.insert(_make(name="wrong-agent", space="s-shard", agent="agent-2"))
    t.insert(_make(name="other-space", space="s2", agent="agent-1"))

    rows = t.list(
        agent_uuid="agent-1",
        space_uuids=("s1", "s-user_shared", "s-biz_shared", "s-shard"),
    )

    assert [(row.space_uuid, row.name) for row in rows] == [
        ("s1", "exact"),
        ("s1", "same-name"),
        ("s-user_shared", "user"),
        ("s-biz_shared", "biz"),
        ("s-shard", "shared"),
        ("s-shard", "same-name"),
    ]


def test_update_by_uuid(session):
    t = DiagnoserTable(session)
    d = _make()
    t.insert(d)
    t.update_by_uuid(d.uuid, {"description": "updated", "version": "v2"}, modifier="alice")
    fetched = t.get_by_uuid(d.uuid)
    assert fetched.description == "updated"
    assert fetched.version == "v2"
    assert fetched.modifier == "alice"


def test_delete_by_uuid(session):
    t = DiagnoserTable(session)
    d = _make()
    t.insert(d)
    assert t.delete_by_uuid(d.uuid) is True
    assert t.get_by_uuid(d.uuid) is None
    assert t.delete_by_uuid(d.uuid) is False
