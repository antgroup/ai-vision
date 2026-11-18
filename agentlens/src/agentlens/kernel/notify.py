import httpx

from agentlens.logging import get_logger


def _ip_of(instance_uuid: str) -> str:
    """instance_uuid = 'hostname/ip' -> ip"""
    return instance_uuid.split("/", 1)[1]


class NotifyClient:
    def __init__(
        self, token: str, port: int, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.token = token
        self.port = port
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._log = get_logger("notify")

    async def __aenter__(self) -> "NotifyClient":
        self._client = httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0),
            headers={"x-antmonitor-token": self.token},
        )
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def push_execute(self, worker_uuid: str, task_uuid: str, fenced_token: int) -> bool:
        url = f"http://{_ip_of(worker_uuid)}:{self.port}/internal/execute"
        try:
            r = await self._client.post(
                url, json={"task_uuid": task_uuid, "fenced_token": fenced_token}
            )
            ok = 200 <= r.status_code < 300
            if ok:
                self._log.info(
                    "notify.push_execute_ok",
                    target_worker=worker_uuid,
                    task_uuid=task_uuid,
                )
            return ok
        except Exception as e:  # noqa: BLE001
            self._log.warning("notify.push_execute_fail", err=str(e), target_worker=worker_uuid)
            return False

    async def notify_master(self, master_uuid: str, task_uuid: str) -> bool:
        url = f"http://{_ip_of(master_uuid)}:{self.port}/internal/notify"
        try:
            r = await self._client.post(url, json={"task_uuid": task_uuid})
            ok = 200 <= r.status_code < 300
            if ok:
                self._log.info("notify.master_ok", master_uuid=master_uuid, task_uuid=task_uuid)
            return ok
        except Exception as e:  # noqa: BLE001
            self._log.warning("notify.master_fail", err=str(e), master=master_uuid)
            return False
