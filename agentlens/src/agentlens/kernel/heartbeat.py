import asyncio

from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.logging import get_logger


class HeartbeatLoop:
    def __init__(self, table: HeartbeatTable, hostname: str, ip: str, interval_sec: float) -> None:
        self.table = table
        self.hostname = hostname
        self.ip = ip
        self.interval = interval_sec
        self._log = get_logger("heartbeat")

    async def tick(self) -> None:
        try:
            await asyncio.to_thread(self.table.upsert, hostname=self.hostname, ip=self.ip)
            self._log.info("heartbeat.write_ok")
        except Exception as e:  # noqa: BLE001
            await asyncio.to_thread(self.table.s.rollback)
            self._log.warning("heartbeat.write_fail", err=str(e))

    async def run(self) -> None:
        self._log.info("heartbeat.start", host=self.hostname, ip=self.ip)
        while True:
            await self.tick()
            await asyncio.sleep(self.interval)
