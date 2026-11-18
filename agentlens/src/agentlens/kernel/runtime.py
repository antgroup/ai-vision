import asyncio
from dataclasses import dataclass

from agentlens.config import Settings
from agentlens.infra.ob.engine import make_engine, make_session_factory
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.leader_lease_table import LeaderLeaseTable
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.kernel.elector import LeaderElectorLoop
from agentlens.kernel.heartbeat import HeartbeatLoop
from agentlens.kernel.master.dispatch_loop import DispatchLoop
from agentlens.kernel.master.rescue_loop import RescueLoop
from agentlens.kernel.master.schedule_scanner import ScheduleScanner
from agentlens.kernel.master.task_cleanup_loop import TaskCleanupLoop
from agentlens.kernel.notify import NotifyClient
from agentlens.logging import get_logger


@dataclass
class MasterCoroutines:
    schedule: ScheduleScanner
    dispatch: DispatchLoop
    rescue: RescueLoop
    cleanup: TaskCleanupLoop | None


class Runtime:
    def __init__(self, settings: Settings, session_factory=None) -> None:
        self.settings = settings
        self._log = get_logger("runtime")
        self._sf = session_factory or make_session_factory(
            make_engine(
                settings.ob_url,
                sql_log_enabled=settings.ob_sql_log_enabled,
            )
        )
        self.elector: LeaderElectorLoop | None = None
        self.heartbeat: HeartbeatLoop | None = None
        self.master: MasterCoroutines | None = None
        self._tasks: list[asyncio.Task] = []
        self._master_tasks: list[asyncio.Task] = []
        self._master_antcollector = None
        self._master_cleanup_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        from agentlens.diagnoser.executors.rule.custom.pool import (
            CustomRuleWorkerPool,
            set_custom_rule_worker_pool,
        )

        pool = CustomRuleWorkerPool(size=self.settings.custom_rule_worker_pool_size)
        pool.start()
        set_custom_rule_worker_pool(pool)
        self._custom_rule_worker_pool = pool

        s_hb = self._sf()
        s_elector = self._sf()

        self.heartbeat = HeartbeatLoop(
            table=HeartbeatTable(s_hb),
            hostname=self.settings.hostname,
            ip=self.settings.ip,
            interval_sec=self.settings.heartbeat_interval_sec,
        )
        self.elector = LeaderElectorLoop(
            table=LeaderLeaseTable(s_elector),
            me=self.settings.instance_uuid,
            ttl_sec=self.settings.lease_ttl_sec,
            interval_sec=self.settings.heartbeat_interval_sec,
        )
        self.elector.on_role_change(self._on_role_change)

        s_recover = self._sf()
        recovered, failed = TaskTable(s_recover).reset_orphaned_by_worker(
            self.settings.instance_uuid,
            max_task_attempts=self.settings.max_task_attempts,
        )
        s_recover.close()
        if recovered or failed:
            self._log.info(
                "runtime.recover_orphaned_tasks",
                worker=self.settings.instance_uuid,
                recovered=recovered,
                failed=failed,
                max_task_attempts=self.settings.max_task_attempts,
            )

        self._tasks.append(
            asyncio.create_task(self._reconcile_sandbox_leases(), name="sandbox-cleanup")
        )
        self._tasks.append(asyncio.create_task(self.heartbeat.run(), name="hb"))
        self._tasks.append(asyncio.create_task(self.elector.run(), name="elector"))
        self._log.info(
            "runtime.start",
            me=self.settings.instance_uuid,
            heartbeat_sec=self.settings.heartbeat_interval_sec,
            lease_ttl_sec=self.settings.lease_ttl_sec,
            master_execute=self.settings.master_execute_tasks,
        )

    async def _reconcile_sandbox_leases(self) -> None:
        from agentlens.service.sandbox_execution import SandboxExecutionService

        service = SandboxExecutionService(self._sf)
        while True:
            try:
                # One bounded network cleanup at a time; do not hold DB sessions across HTTP.
                await asyncio.to_thread(service.reconcile_pending, batch_limit=1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.warning("sandbox.cleanup_retry_failed", error_type=type(exc).__name__)
            await asyncio.sleep(self.settings.sandbox_cleanup_interval_sec)

    async def stop(self) -> None:
        self._kill_master()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._master_cleanup_tasks:
            await asyncio.gather(*list(self._master_cleanup_tasks), return_exceptions=True)

        from agentlens.diagnoser.executors.rule.custom.pool import set_custom_rule_worker_pool

        if hasattr(self, "_custom_rule_worker_pool") and self._custom_rule_worker_pool is not None:
            self._custom_rule_worker_pool.stop()
            set_custom_rule_worker_pool(None)
            self._custom_rule_worker_pool = None

        self._log.info("runtime.stop")

    def _on_role_change(self, is_leader: bool) -> None:
        if is_leader:
            self._spawn_master()
        else:
            self._kill_master()

    def _spawn_master(self) -> None:
        if self.master is not None:
            return
        elector = self.elector
        if elector is None:
            raise RuntimeError("leader elector is not initialized")
        s_sched = self._sf()
        s_dispatch = self._sf()
        s_rescue = self._sf()
        s_cleanup = self._sf() if self.settings.task_cleanup_enabled else None

        class _NotifyAdapter:
            def __init__(self, token: str, port: int) -> None:
                self._token = token
                self._port = port

            async def push_execute(
                self, worker_uuid: str, task_uuid: str, fenced_token: int
            ) -> bool:
                async with NotifyClient(token=self._token, port=self._port) as c:
                    return await c.push_execute(worker_uuid, task_uuid, fenced_token)

        # master 执行自己的任务时：派给自己的任务进程内直跑，绕开 HTTP loopback 自推
        # （自连 bridge IP / 端口漂移 / 连接超时都可能让自推失败，进程内执行不依赖网络）。
        local_execute = None
        self._master_antcollector = None
        if self.settings.master_execute_tasks:
            local_execute, self._master_antcollector = self._make_local_execute()

        self.master = MasterCoroutines(
            schedule=ScheduleScanner(
                job_table=JobTable(s_sched),
                task_table=TaskTable(s_sched),
                interval_sec=self.settings.dispatch_loop_interval_sec,
            ),
            dispatch=DispatchLoop(
                task_table=TaskTable(s_dispatch),
                heartbeat_table=HeartbeatTable(s_dispatch),
                notify=_NotifyAdapter(self.settings.token, self.settings.port),
                self_uuid=self.settings.instance_uuid,
                fenced_token_provider=lambda: elector.fenced_token,
                master_execute_tasks=self.settings.master_execute_tasks,
                local_execute=local_execute,
                interval_sec=self.settings.dispatch_loop_interval_sec,
                alive_threshold_sec=self.settings.lease_ttl_sec,
                local_execute_concurrency=self.settings.master_execute_concurrency,
                worker_execute_concurrency=self.settings.worker_execute_concurrency,
            ),
            rescue=RescueLoop(
                task_table=TaskTable(s_rescue),
                heartbeat_table=HeartbeatTable(s_rescue),
                dispatch_timeout_sec=self.settings.dispatch_timeout_sec,
                alive_threshold_sec=self.settings.lease_ttl_sec,
                max_task_attempts=self.settings.max_task_attempts,
                interval_sec=self.settings.rescue_loop_interval_sec,
            ),
            cleanup=(
                TaskCleanupLoop(
                    task_table=TaskTable(s_cleanup),
                    retention_days=self.settings.task_retention_days,
                    interval_sec=self.settings.task_cleanup_interval_sec,
                    batch_size=self.settings.task_cleanup_batch_size,
                )
                if s_cleanup is not None
                else None
            ),
        )
        self._master_tasks.append(asyncio.create_task(self.master.schedule.run(), name="sched"))
        self._master_tasks.append(asyncio.create_task(self.master.dispatch.run(), name="dispatch"))
        self._master_tasks.append(asyncio.create_task(self.master.rescue.run(), name="rescue"))
        if self.master.cleanup is not None:
            self._master_tasks.append(
                asyncio.create_task(self.master.cleanup.run(), name="task-cleanup")
            )
        self._log.info(
            "runtime.master_spawn",
            dispatch_interval=self.settings.dispatch_loop_interval_sec,
            rescue_interval=self.settings.rescue_loop_interval_sec,
            dispatch_timeout=self.settings.dispatch_timeout_sec,
            max_task_attempts=self.settings.max_task_attempts,
            task_cleanup_enabled=self.settings.task_cleanup_enabled,
            task_retention_days=self.settings.task_retention_days,
            task_cleanup_interval_sec=self.settings.task_cleanup_interval_sec,
            task_cleanup_batch_size=self.settings.task_cleanup_batch_size,
        )

    def _make_local_execute(self):
        """构造「进程内执行 task」的回调（master 给自己派单时用，绕开 HTTP 自推）。

        与 /internal/execute 等价：建一个 Runner 跑该 task，runner 自行 CAS 状态流转。
        遥测读 client（SigNoz OB）与 OTLP 写 client 各建一次复用（TracerProvider 不宜每次新建）；
        每次执行用独立 session（runner 会改 task 状态），用完即关。
        """
        from agentlens.infra.antcollector import HttpAntcollectorClient
        from agentlens.infra.signoz.dialect import SignozObDialect
        from agentlens.infra.signoz.ob_client import SignozObClient
        from agentlens.kernel.worker.runner import Runner
        from agentlens.service.datasource.signoz import TraceStore
        from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
        from agentlens.service.ingest_exporter import make_ingest_exporter
        from agentlens.service.task_stats_store import TaskStatsStore

        s = self.settings
        sg = SignozObClient(
            s.signoz_ob_url,
            timeout_sec=s.signoz_query_timeout_sec,
            sql_log_enabled=s.ob_sql_log_enabled,
        )
        dialect = SignozObDialect(org_id=s.signoz_org_id)
        ac = HttpAntcollectorClient(
            otlp_endpoint=s.antcollector_url,
            access_id=s.antcollector_access_id,
            access_token=s.antcollector_access_token,
            span_exporter=make_ingest_exporter(s, self._sf),
        )

        async def _local_execute(task_uuid: str) -> None:
            sess = self._sf()
            try:
                runner = Runner(
                    task_table=TaskTable(sess),
                    job_table=JobTable(sess),
                    dataset_table=DatasetTable(sess),
                    diagnoser_table=DiagnoserTable(sess),
                    model_connection_table=ModelConnectionTable(sess),
                    trace_store=TraceStore(sg, dialect=dialect),
                    record_store=DiagnosisRecordStore(
                        sg, ac, table=s.diagnosis_record_table, dialect=dialect
                    ),
                    task_stats_store=TaskStatsStore(
                        sg, ac, table=s.diagnosis_record_table, dialect=dialect
                    ),
                    self_uuid=s.instance_uuid,
                    session_factory=self._sf,
                )
                await runner.run(task_uuid)
            finally:
                sess.close()

        return _local_execute, ac

    def _kill_master(self) -> None:
        if self.master is None:
            return
        master = self.master
        tasks_to_cancel = list(self._master_tasks)
        for t in tasks_to_cancel:
            t.cancel()
        self._master_tasks = []
        self.master = None
        antcollector = self._master_antcollector
        self._master_antcollector = None
        self._log.info("runtime.master_kill")
        cleanup = asyncio.create_task(
            self._finish_master_shutdown(
                tasks_to_cancel,
                master.dispatch,
                antcollector,
            ),
            name="master-cleanup",
        )
        self._master_cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(self._on_master_cleanup_done)

    async def _finish_master_shutdown(self, tasks, dispatch, antcollector) -> None:
        await asyncio.gather(*tasks, return_exceptions=True)
        await dispatch.wait_local_tasks()
        if antcollector is not None:
            await asyncio.to_thread(antcollector.close)

    def _on_master_cleanup_done(self, task: asyncio.Task) -> None:
        self._master_cleanup_tasks.discard(task)
        try:
            task.result()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("runtime.master_cleanup_fail", error=str(exc))
