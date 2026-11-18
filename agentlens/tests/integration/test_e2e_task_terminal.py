from datetime import datetime

import pytest

from agentlens.domain.enums import TaskStatus
from agentlens.domain.task import Task
from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.kernel.master.rescue_loop import RescueLoop
from agentlens.utils.uuid_gen import new_task_uuid


@pytest.mark.asyncio
async def test_failed_task_is_terminal(session):
    uid = new_task_uuid()
    TaskTable(session).insert(
        Task(
            uuid=uid,
            job_uuid="j-aaaaaaaa",
            status=TaskStatus.FAILED,
            worker=None,
            scheduled_at=datetime.now(),
            started_at=None,
            finished_at=None,
            attempt=1,
            msg="prev fail",
            gmt_create=None,
            gmt_modified=None,
            creator=None,
            modifier=None,
        )
    )
    loop = RescueLoop(
        task_table=TaskTable(session),
        heartbeat_table=HeartbeatTable(session),
        dispatch_timeout_sec=60,
        alive_threshold_sec=30,
    )
    await loop.tick()
    t = TaskTable(session).get_by_uuid(uid)
    assert t.status == TaskStatus.FAILED
    assert t.worker is None
    assert t.msg == "prev fail"
