from agentlens.config import Settings


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("AGENTLENS_OB_URL", "mysql+pymysql://u:p@h/d")
    monkeypatch.setenv("AGENTLENS_TOKEN", "sek")
    monkeypatch.setenv("AGENTLENS_PORT", "8080")
    monkeypatch.setenv("AGENTLENS_HOSTNAME", "node-1")
    monkeypatch.setenv("AGENTLENS_IP", "10.0.0.1")
    monkeypatch.setenv("SIGNOZ_OB_URL", "mysql+pymysql://u:p@h:2881/signoz_telemetry")
    monkeypatch.setenv("SIGNOZ_ORG_ID", "org-1")
    monkeypatch.setenv("SIGNOZ_SPANS_TABLE", "ns1_trace")
    monkeypatch.setenv("ANTCOLLECTOR_ENDPOINT", "https://ac.test/v1/traces")
    monkeypatch.setenv("OB_SQL_LOG_ENABLED", "true")
    monkeypatch.setenv("AGENTLENS_LOG_RETENTION_DAYS", "14")
    monkeypatch.setenv("CUSTOM_RULE_WORKER_POOL_SIZE", "4")
    monkeypatch.setenv("EXEC_TIMEOUT", "7.5")

    s = Settings()
    assert s.ob_url == "mysql+pymysql://u:p@h/d"
    assert s.token == "sek"
    assert s.port == 8080
    assert s.hostname == "node-1"
    assert s.ip == "10.0.0.1"
    assert s.instance_uuid == "node-1/10.0.0.1"
    assert s.signoz_org_id == "org-1"
    assert s.diagnosis_record_table == "ns1_trace"
    assert s.antcollector_url == "https://ac.test/v1/traces"
    assert "u:p@" not in s.to_safe_dict()["signoz_ob_url"]
    assert s.ob_sql_log_enabled is True
    assert s.log_retention_days == 14
    assert s.custom_rule_worker_pool_size == 4
    assert s.exec_timeout == 7.5


def test_intervals_have_sensible_defaults(monkeypatch):
    monkeypatch.setenv("AGENTLENS_OB_URL", "x")
    monkeypatch.setenv("AGENTLENS_TOKEN", "x")
    monkeypatch.setenv("AGENTLENS_HOSTNAME", "h")
    monkeypatch.setenv("AGENTLENS_IP", "i")
    monkeypatch.setenv("SIGNOZ_OB_URL", "x")
    monkeypatch.setenv("SIGNOZ_ORG_ID", "x")
    monkeypatch.setenv("ANTCOLLECTOR_ENDPOINT", "x")
    s = Settings(_env_file=None)
    assert s.signoz_spans_table == "signoz_traces"
    assert s.heartbeat_interval_sec == 30
    assert s.lease_ttl_sec == 120
    assert s.dispatch_loop_interval_sec == 10
    assert s.rescue_loop_interval_sec == 60
    assert s.dispatch_timeout_sec == 300
    assert s.max_task_attempts == 2
    assert s.task_cleanup_enabled is True
    assert s.task_retention_days == 30
    assert s.task_cleanup_interval_sec == 600
    assert s.task_cleanup_batch_size == 1000
    assert s.master_execute_concurrency == 10
    assert s.worker_execute_concurrency == 10
    assert s.custom_rule_worker_pool_size == 3
    assert s.exec_timeout == 5.0
    assert s.log_retention_days == 7
