import re

from agentlens.utils.uuid_gen import (
    new_dataset_uuid,
    new_diagnoser_uuid,
    new_job_uuid,
    new_task_uuid,
)


def test_prefixes():
    assert new_diagnoser_uuid().startswith("dg-")
    assert new_dataset_uuid().startswith("ds-")
    assert new_job_uuid().startswith("j-")
    assert new_task_uuid().startswith("t-")


def test_random_part_length_and_charset():
    for fn in (new_diagnoser_uuid, new_dataset_uuid, new_job_uuid, new_task_uuid):
        u = fn()
        suffix = u.split("-", 1)[1]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-z0-9]{8}", suffix)


def test_uniqueness_smoke():
    uniq = {new_task_uuid() for _ in range(10_000)}
    assert len(uniq) == 10_000  # 8 字符 base36 撞概率极低
