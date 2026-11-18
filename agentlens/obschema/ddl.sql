-- AgentLens 建表 DDL（OceanBase）
-- 提交 ODC 工单执行

-- 1. 诊断器
CREATE TABLE `agentlens_diagnoser` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '业务唯一标识',
  `space_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '空间UUID',
  `agent_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Agent UUID',
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '诊断器名称',
  `description` text COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '描述',
  `type` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '类型: builtin/custom',
  `mode` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '模式: rule/llm',
  `spec` json NOT NULL COMMENT '诊断器配置',
  `version` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'v1' COMMENT '版本号',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '创建人',
  `modifier` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_diagnoser_uuid` (`uuid`) BLOCK_SIZE 16384 GLOBAL,
  UNIQUE KEY `uk_diagnoser_san` (`space_uuid`, `agent_uuid`, `name`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0 COMMENT = '诊断器表';

-- 2. 模型连接（空间级，与 Agent 无关）
CREATE TABLE `agentlens_model_connection` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '业务唯一标识',
  `space_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '所属空间UUID',
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '连接名称',
  `endpoint` varchar(1024) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '模型服务地址',
  `model_name` varchar(256) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '模型标识',
  `api_key` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '只写密钥',
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'untested/available/unavailable',
  `diagnostic_validation` json DEFAULT NULL COMMENT 'Bounded basic diagnostic validation report',
  `config_revision` bigint(20) NOT NULL DEFAULT 1 COMMENT 'Configuration compare-and-swap revision',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '创建人',
  `modifier` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_model_connection_uuid` (`uuid`) BLOCK_SIZE 16384 GLOBAL,
  UNIQUE KEY `uk_model_connection_sn` (`space_uuid`, `name`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_model_connection_space` (`space_uuid`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0 COMMENT = '空间级模型连接表';

-- 3. 数据集
CREATE TABLE `agentlens_dataset` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '业务唯一标识',
  `space_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '空间UUID',
  `agent_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Agent UUID',
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '数据集名称',
  `description` text COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '描述',
  `type` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '数据源类型: signoz',
  `spec` json NOT NULL COMMENT '查询配置',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '创建人',
  `modifier` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_dataset_uuid` (`uuid`) BLOCK_SIZE 16384 GLOBAL,
  UNIQUE KEY `uk_dataset_san` (`space_uuid`, `agent_uuid`, `name`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0 COMMENT = '数据集表';

-- 4. 任务定义
CREATE TABLE `agentlens_job` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '业务唯一标识',
  `space_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '空间UUID',
  `agent_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Agent UUID',
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Job名称',
  `description` text COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '描述',
  `trigger_type` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '触发类型: manual/schedule',
  `schedule_cron` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'cron表达式',
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'enabled' COMMENT '状态: enabled/disabled',
  `dataset_uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '关联数据集UUID',
  `diagnoser_uuids` json NOT NULL COMMENT '关联诊断器UUID列表',
  `config` json DEFAULT NULL COMMENT 'Job扩展配置',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '创建人',
  `modifier` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_job_uuid` (`uuid`) BLOCK_SIZE 16384 GLOBAL,
  UNIQUE KEY `uk_job_san` (`space_uuid`, `agent_uuid`, `name`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0 COMMENT = '任务定义表';

-- 5. 任务执行
CREATE TABLE `agentlens_task` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '业务唯一标识',
  `job_uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '所属Job UUID',
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending' COMMENT '状态: pending/dispatched/running/succeeded/failed/canceled',
  `worker` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '执行节点标识',
  `scheduled_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'cron逻辑时间或手动触发时间',
  `started_at` timestamp NULL DEFAULT NULL COMMENT '开始执行时间',
  `finished_at` timestamp NULL DEFAULT NULL COMMENT '完成时间',
  `attempt` int(11) NOT NULL DEFAULT 0 COMMENT '已执行次数',
  `msg` text COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '执行日志',
  `execution_snapshot` json DEFAULT NULL COMMENT '冻结有效执行定义，不含运行时凭证',
  `judge_request_ledger` json DEFAULT NULL COMMENT '有界请求认领与响应usage，不含请求正文',
  `stats` json DEFAULT NULL COMMENT '一次触发统计: {source,diagnosers}，诊断器下按 metric 分组计数',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '创建人',
  `modifier` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task_uuid` (`uuid`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_task_job_sched` (`job_uuid`, `scheduled_at`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_task_status_sched` (`status`, `scheduled_at`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_task_status_finished_id` (`status`, `finished_at`, `id`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_task_worker_status` (`worker`, `status`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0 COMMENT = '任务执行表';

-- 6. 心跳
CREATE TABLE `agentlens_heartbeat` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '实例标识: hostname/ip',
  `hostname` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '主机名',
  `ip` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT 'IP地址',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后心跳时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_heartbeat_uuid` (`uuid`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_heartbeat_gmt_modified` (`gmt_modified`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0 COMMENT = '实例心跳表';

-- 7. 选主租约（单行）
CREATE TABLE `agentlens_leader_lease` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '业务唯一标识',
  `lock_key` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '锁标识',
  `holder_uuid` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '' COMMENT '持有者实例标识',
  `acquired_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '获取租约时间',
  `expires_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '租约过期时间',
  `fenced_token` bigint(20) NOT NULL DEFAULT 0 COMMENT '防护令牌(单调递增)',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '创建人',
  `modifier` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '修改人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_leader_lease_uuid` (`uuid`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_leader_lease_lockkey` (`lock_key`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0 COMMENT = '选主租约表';

-- 8. 初始化选主种子行
INSERT INTO `agentlens_leader_lease` (`uuid`, `lock_key`, `holder_uuid`, `acquired_at`, `expires_at`, `fenced_token`)
VALUES ('lease-master-seed', 'master', '__bootstrap__', NOW(), NOW(), 0);

-- 9. alembic 版本追踪表
CREATE TABLE `alembic_version` (
  `version_num` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '迁移版本号',
  PRIMARY KEY (`version_num`)
) DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0 COMMENT = 'Alembic迁移版本追踪';

-- Optional authenticated sandbox integration resources (migration 012).
CREATE TABLE `agentlens_sandbox_connection` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `space_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `provider` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `endpoint` varchar(1024) COLLATE utf8mb4_unicode_ci NOT NULL,
  `api_key` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `image` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `cpu` int(11) NOT NULL,
  `memory` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `timeout_seconds` int(11) NOT NULL,
  `config_revision` bigint(20) NOT NULL,
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `modifier` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sandbox_connection_uuid` (`uuid`) BLOCK_SIZE 16384 GLOBAL,
  UNIQUE KEY `uk_sandbox_connection_sn` (`space_uuid`, `name`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_sandbox_connection_space` (`space_uuid`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0;

CREATE TABLE `agentlens_sandbox_binding` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `space_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `connection_uuid` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `require_sandbox` int(11) NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `modifier` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sandbox_binding_space` (`space_uuid`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0;

CREATE TABLE `agentlens_sandbox_lease` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `connection_uuid` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `space_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `task_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `attempt` int(11) NOT NULL,
  `diagnoser_uuid` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `request_fingerprint` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `state` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `cleanup_verified` int(11) NOT NULL,
  `expires_at` timestamp NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `creator` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `modifier` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sandbox_lease_uuid` (`uuid`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_sandbox_lease_connection_state` (`connection_uuid`, `state`) BLOCK_SIZE 16384 GLOBAL,
  KEY `idx_sandbox_lease_task_attempt` (`task_uuid`, `attempt`) BLOCK_SIZE 16384 GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.0' REPLICA_NUM = 2 BLOCK_SIZE = 16384 USE_BLOOM_FILTER = FALSE TABLET_SIZE = 134217728 PCTFREE = 0;

-- Fresh schema includes the 009/010 Task columns, 011 model validation and 012 sandbox tables.
-- Migration 008 transforms pre-existing Dataset rows; a fresh schema has no Dataset rows.
INSERT INTO `alembic_version` (`version_num`) VALUES ('012');
