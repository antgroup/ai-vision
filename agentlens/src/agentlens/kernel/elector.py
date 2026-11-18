import asyncio
from collections.abc import Callable

from agentlens.infra.ob.tables.leader_lease_table import LeaderLeaseTable
from agentlens.logging import get_logger


class LeaderElectorLoop:
    def __init__(self, table: LeaderLeaseTable, me: str, ttl_sec: int, interval_sec: float) -> None:
        self.table = table
        self.me = me
        self.ttl = ttl_sec
        self.interval = interval_sec
        self.is_leader = False
        self.fenced_token = 0
        self._callbacks: list[Callable[[bool], None]] = []
        self._log = get_logger("elector")

    def on_role_change(self, cb: Callable[[bool], None]) -> None:
        self._callbacks.append(cb)

    async def tick(self) -> None:
        res = await asyncio.to_thread(self.table.try_acquire_or_renew, me=self.me, ttl_sec=self.ttl)
        new_role = res.is_leader
        self.fenced_token = res.fenced_token
        self._log.info(
            "elector.tick",
            is_leader=new_role,
            fenced_token=res.fenced_token,
            current_holder=res.holder_uuid,
        )
        if new_role != self.is_leader:
            old = self.is_leader
            self.is_leader = new_role
            self._log.info(
                "elector.role_change",
                was_leader=old,
                is_leader=new_role,
                fenced_token=res.fenced_token,
                current_holder=res.holder_uuid,
            )
            for cb in list(self._callbacks):
                try:
                    cb(new_role)
                except Exception as e:  # noqa: BLE001
                    self._log.warning("elector.callback_fail", err=str(e))

    async def run(self) -> None:
        self._log.info("elector.start", me=self.me)
        while True:
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001
                await asyncio.to_thread(self.table.s.rollback)
                self._log.warning("elector.tick_fail", err=str(e))
            await asyncio.sleep(self.interval)
