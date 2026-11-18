import pytest
from sqlalchemy.exc import IntegrityError

from agentlens.domain.dataset import Dataset
from agentlens.domain.enums import DatasetType
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.utils.uuid_gen import new_dataset_uuid


def _make(name="x", space="s", agent="a") -> Dataset:
    return Dataset(
        uuid=new_dataset_uuid(),
        space_uuid=space,
        agent_uuid=agent,
        name=name,
        description="",
        type=DatasetType.SIGNOZ,
        spec={"signoz": {}, "query": {"table": "trace"}},
        gmt_create=None,
        gmt_modified=None,
        creator="u",
        modifier="u",
    )


def test_insert_get_update_delete(session):
    t = DatasetTable(session)
    d = _make()
    t.insert(d)
    assert t.get_by_uuid(d.uuid).name == "x"
    t.update_by_uuid(d.uuid, {"description": "n"}, modifier="bob")
    assert t.get_by_uuid(d.uuid).description == "n"
    assert t.delete_by_uuid(d.uuid) is True
    assert t.get_by_uuid(d.uuid) is None


def test_list_by_agent(session):
    t = DatasetTable(session)
    t.insert(_make(name="a1", agent="A"))
    t.insert(_make(name="a2", agent="A"))
    t.insert(_make(name="b1", agent="B"))
    rows = t.list(space_uuid="s", agent_uuid="A")
    assert {r.name for r in rows} == {"a1", "a2"}


def test_list_selected_spaces_keeps_exact_agent_and_order(session):
    table = DatasetTable(session)
    table.insert(_make(name="local", space="s", agent="a"))
    table.insert(_make(name="user", space="s-user_shared", agent="a"))
    table.insert(_make(name="biz", space="s-biz_shared", agent="a"))
    table.insert(_make(name="shared", space="s-shard", agent="a"))
    table.insert(_make(name="wrong-agent", space="s-shard", agent="b"))

    rows = table.list(
        agent_uuid="a",
        space_uuids=("s", "s-user_shared", "s-biz_shared", "s-shard"),
    )

    assert [(row.space_uuid, row.name) for row in rows] == [
        ("s", "local"),
        ("s-user_shared", "user"),
        ("s-biz_shared", "biz"),
        ("s-shard", "shared"),
    ]


def test_duplicate_name_collides(session):
    t = DatasetTable(session)
    t.insert(_make(name="dup"))
    with pytest.raises(IntegrityError):
        t.insert(_make(name="dup"))
