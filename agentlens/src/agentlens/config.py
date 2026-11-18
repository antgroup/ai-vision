import logging
import os
import socket
from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_boot_log = logging.getLogger("agentlens.config")


def _get_local_ip() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except socket.gaierror:
        return "127.0.0.1"


def _resolve_env_file() -> str:
    """根据 AIVISION_ENV 选择对应的 env 文件。

    - 空 / prepub / gray / prod -> env/env.prod
    - dev                       -> env/env.dev
    """
    base_dir = Path(__file__).resolve().parent.parent.parent
    app_env = os.environ.get("AIVISION_ENV", "")

    if app_env == "dev":
        env_rel = "env/env.dev"
    else:
        # 空 / prepub / gray / prod / 未知 -> env.prod
        env_rel = "env/env.prod"

    env_file = base_dir / env_rel
    _boot_log.info(
        "config.resolve_env: AIVISION_ENV=%r, base_dir=%s, env_file=%s, exists=%s",
        app_env,
        base_dir,
        env_file,
        env_file.exists(),
    )
    if env_file.exists():
        return str(env_file)
    _boot_log.warning("config.resolve_env: env file not found, using defaults only")
    return ""


class SandboxInputSettings(BaseSettings):
    """Read the operator bound without resolving service identity or credentials."""

    model_config = SettingsConfigDict(extra="ignore", env_file_encoding="utf-8")
    sandbox_max_input_bytes: int = Field(
        8 * 1024**2, ge=1024, le=16 * 1024**2, alias="SANDBOX_MAX_INPUT_BYTES"
    )


def sandbox_input_limit() -> int:
    return SandboxInputSettings(_env_file=_resolve_env_file()).sandbox_max_input_bytes


class Settings(SandboxInputSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file_encoding="utf-8",
        extra="ignore",
        # Compiled methods have Cython's callable type rather than FunctionType.
        ignored_types=(type(_get_local_ip),),
    )

    def __init__(self, **kwargs):
        env_file = _resolve_env_file()
        kwargs.setdefault("_env_file", env_file)
        super().__init__(**kwargs)
        self._resolved_env_file = env_file

    # OceanBase / MySQL：完整 URL 优先，否则从 DB_* 拼接连接串。
    ob_url_override: str = Field("", alias="AGENTLENS_OB_URL")
    db_engine: str = Field("mysql+pymysql", alias="DB_ENGINE")
    db_host: str = Field("127.0.0.1", alias="DB_HOST")
    db_port: int = Field(2881, alias="DB_PORT")
    db_user: str = Field("root", alias="DB_USER")
    db_password: str = Field("", alias="DB_PASSWORD")
    db_name: str = Field("agentlens", alias="DB_NAME")
    ob_sql_log_enabled: bool = Field(False, alias="OB_SQL_LOG_ENABLED")

    @property
    def ob_url(self) -> str:
        if self.ob_url_override:
            return self.ob_url_override
        from urllib.parse import quote_plus

        user = quote_plus(self.db_user)
        pwd = quote_plus(self.db_password) if self.db_password else ""
        cred = f"{user}:{pwd}@" if pwd else f"{user}@"
        return f"{self.db_engine}://{cred}{self.db_host}:{self.db_port}/{self.db_name}"

    # Auth
    token: str = Field(alias="AGENTLENS_TOKEN")
    # Server
    port: int = Field(8080, alias="AGENTLENS_PORT")
    hostname: str = Field(default_factory=socket.gethostname, alias="AGENTLENS_HOSTNAME")
    ip: str = Field(default_factory=_get_local_ip, alias="AGENTLENS_IP")
    # SigNoz 遥测库（OceanBase, MySQL 协议）：原始 Trace、DiagnosisRecord、TaskStats 的统一读取层
    signoz_ob_url: str = Field(alias="SIGNOZ_OB_URL")
    signoz_org_id: str = Field(alias="SIGNOZ_ORG_ID")
    signoz_spans_table: str = Field("signoz_traces", alias="SIGNOZ_SPANS_TABLE")
    signoz_query_timeout_sec: float = Field(30.0, gt=0, alias="SIGNOZ_QUERY_TIMEOUT_SEC")
    # OTLP 上报通道（SigNoz Collector 的 OTLP/HTTP traces 完整地址）
    antcollector_endpoint: str = Field(alias="ANTCOLLECTOR_ENDPOINT")
    antcollector_access_id: str = Field("", alias="ANTCOLLECTOR_ACCESS_ID")
    antcollector_access_token: str = Field("", alias="ANTCOLLECTOR_ACCESS_TOKEN")
    aivision_ingest_token_url: str = Field("", alias="AIVISION_INGEST_TOKEN_URL")

    @property
    def antcollector_url(self) -> str:
        return self.antcollector_endpoint

    @property
    def diagnosis_record_table(self) -> str:
        return self.signoz_spans_table

    # Intervals (seconds)
    heartbeat_interval_sec: float = Field(30, alias="HEARTBEAT_INTERVAL_SEC")
    lease_ttl_sec: float = Field(120, alias="LEASE_TTL_SEC")
    dispatch_loop_interval_sec: float = Field(10, alias="DISPATCH_LOOP_INTERVAL_SEC")
    rescue_loop_interval_sec: float = Field(60, alias="RESCUE_LOOP_INTERVAL_SEC")
    dispatch_timeout_sec: float = Field(300, alias="DISPATCH_TIMEOUT_SEC")
    max_task_attempts: int = Field(2, ge=1, alias="MAX_TASK_ATTEMPTS")
    task_cleanup_enabled: bool = Field(True, alias="TASK_CLEANUP_ENABLED")
    task_retention_days: int = Field(30, ge=1, alias="TASK_RETENTION_DAYS")
    task_cleanup_interval_sec: float = Field(600, gt=0, alias="TASK_CLEANUP_INTERVAL_SEC")
    task_cleanup_batch_size: int = Field(1000, ge=1, alias="TASK_CLEANUP_BATCH_SIZE")
    master_execute_tasks: bool = Field(False, alias="MASTER_EXECUTE_TASKS")
    master_execute_concurrency: int = Field(10, ge=1, alias="MASTER_EXECUTE_CONCURRENCY")
    worker_execute_concurrency: int = Field(10, ge=1, alias="WORKER_EXECUTE_CONCURRENCY")
    # Custom Rule sandbox
    custom_rule_worker_pool_size: int = Field(
        3,
        ge=1,
        alias="CUSTOM_RULE_WORKER_POOL_SIZE",
    )
    exec_timeout: float = Field(5.0, gt=0, alias="EXEC_TIMEOUT")
    enhanced_exec_timeout: float = Field(30.0, gt=0, le=300, alias="ENHANCED_EXEC_TIMEOUT")
    sandbox_cleanup_interval_sec: float = Field(30, gt=0, alias="SANDBOX_CLEANUP_INTERVAL_SEC")
    rule_circuit_breaker_threshold: int = Field(
        5,
        alias="RULE_CIRCUIT_BREAKER_THRESHOLD",
    )
    # 首次访问一个空的具体 scope（space + agent）时自动补齐的内置 Rule 场景，逗号分隔；
    # 置空则关闭自动补齐，只保留 sync-*-builtins 手工同步。
    default_builtin_scenarios: str = Field(
        "common,coding",
        alias="AGENTLENS_DEFAULT_BUILTIN_SCENARIOS",
    )

    @property
    def default_builtin_scenario_list(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item.strip() for item in self.default_builtin_scenarios.split(",") if item.strip()
            )
        )

    # Logging
    log_dir: str = Field("logs", alias="AGENTLENS_LOG_DIR")
    log_level: str = Field("INFO", alias="AGENTLENS_LOG_LEVEL")
    log_stdout: bool = Field(False, alias="AGENTLENS_LOG_STDOUT")
    log_retention_days: int = Field(7, ge=1, alias="AGENTLENS_LOG_RETENTION_DAYS")

    @cached_property
    def instance_uuid(self) -> str:
        return f"{self.hostname}/{self.ip}"

    def log_loaded_config(self) -> None:
        _boot_log.info("config.loaded: %s", self.to_safe_dict())

    def to_safe_dict(self) -> dict:
        return {
            "env_file": self._resolved_env_file,
            "db_host": self.db_host,
            "db_port": self.db_port,
            "db_user": self.db_user,
            "db_name": self.db_name,
            "ob_sql_log_enabled": self.ob_sql_log_enabled,
            "ob_url": self.ob_url.split("@")[-1] if "@" in self.ob_url else self.ob_url,
            "token": self.token[:4] + "***",
            "port": self.port,
            "hostname": self.hostname,
            "ip": self.ip,
            "instance_uuid": self.instance_uuid,
            "signoz_ob_url": (
                self.signoz_ob_url.split("@")[-1]
                if "@" in self.signoz_ob_url
                else self.signoz_ob_url
            ),
            "signoz_org_id": self.signoz_org_id,
            "signoz_spans_table": self.signoz_spans_table,
            "signoz_query_timeout_sec": self.signoz_query_timeout_sec,
            "antcollector_endpoint": self.antcollector_endpoint,
            "antcollector_access_id": self.antcollector_access_id,
            "default_builtin_scenarios": list(self.default_builtin_scenario_list),
            "heartbeat_interval_sec": self.heartbeat_interval_sec,
            "lease_ttl_sec": self.lease_ttl_sec,
            "dispatch_loop_interval_sec": self.dispatch_loop_interval_sec,
            "rescue_loop_interval_sec": self.rescue_loop_interval_sec,
            "dispatch_timeout_sec": self.dispatch_timeout_sec,
            "max_task_attempts": self.max_task_attempts,
            "sandbox_cleanup_interval_sec": self.sandbox_cleanup_interval_sec,
            "enhanced_exec_timeout": self.enhanced_exec_timeout,
            "sandbox_max_input_bytes": self.sandbox_max_input_bytes,
            "task_cleanup_enabled": self.task_cleanup_enabled,
            "task_retention_days": self.task_retention_days,
            "task_cleanup_interval_sec": self.task_cleanup_interval_sec,
            "task_cleanup_batch_size": self.task_cleanup_batch_size,
            "master_execute_tasks": self.master_execute_tasks,
            "master_execute_concurrency": self.master_execute_concurrency,
            "worker_execute_concurrency": self.worker_execute_concurrency,
            "log_dir": self.log_dir,
            "log_level": self.log_level,
            "log_stdout": self.log_stdout,
            "log_retention_days": self.log_retention_days,
        }
