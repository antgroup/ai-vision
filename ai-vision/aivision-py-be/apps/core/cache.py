"""
OB 配置类数据的本地内存缓存。

使用 Django LocMemCache，通过 post_save / post_delete signal 主动失效，
实现类似 list-watch 的效果：首次查库加载，模型变更时立即清除缓存。

跨进程失效：通过 DB 中的 cache_version 字段实现。signal handler 在清
本地缓存的同时 bump version，其他进程通过轮询发现 version 变化后清
自己的本地缓存。轮询间隔由 _VERSION_CHECK_INTERVAL 控制（默认 30s）。
"""

import time
import logging
from django.core.cache import cache, caches
from django.core.cache.backends.locmem import LocMemCache
from django.db.models.signals import post_save, post_delete
from django.db import connection

logger = logging.getLogger(__name__)

# 模型名 → 关联的缓存 key 前缀列表
MODEL_CACHE_PREFIXES = {
    "AgentTypeConfig": ["agent_types:", "agent_type:"],
    "DataSource": ["datasources:"],
    "AgentDashboardPanel": ["panels:"],
}


_CACHE_TTL_KEY = "system:cache_ttl"
_CACHE_TTL_SELF_TIMEOUT = 300  # TTL 配置本身缓存 5 分钟
_DEFAULT_CACHE_TTL = 86400

# --- 跨进程 version 轮询 ---
_VERSION_CHECK_INTERVAL = 30  # 每 30 秒检查一次 DB version
_local_version = 0  # 当前进程记住的 version
_last_version_check = 0.0  # 上次检查的时间戳


def _get_cache_ttl():
    """从 PlatformConfig(platform_code='system') 的 config.cache_ttl 读取全量更新周期（秒）。
    该值本身缓存 5 分钟，改配置后最多 5 分钟生效。"""
    ttl = cache.get(_CACHE_TTL_KEY)
    if ttl is not None:
        return ttl

    try:
        from .models import PlatformConfig

        pc = PlatformConfig.objects.filter(platform_code="system", status=1).first()
        if pc and pc.config:
            ttl = pc.config.get("cache_ttl", _DEFAULT_CACHE_TTL)
        else:
            ttl = _DEFAULT_CACHE_TTL
    except Exception:
        ttl = _DEFAULT_CACHE_TTL

    cache.set(_CACHE_TTL_KEY, ttl, _CACHE_TTL_SELF_TIMEOUT)
    return ttl


def _coerce_version(raw):
    """把 DB 返回的 cache_version 归一成 int。

    OceanBase 连接器把 ``JSON_EXTRACT`` 的数值结果作为字符串返回，且带小数位
    （``'16.0'``），``int('16.0')`` 会抛 ValueError。若在此直接返回 0，版本号
    永远是 0，跨进程失效会静默失灵（只能等 TTL 到期或重启进程），因此这里
    兼容 int / Decimal / '16' / '16.0' 各种形态，并且解析失败要告警而不是吞掉。
    """
    if raw is None or isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        logger.warning("Unparseable cache_version from DB: %r", raw)
        return 0


def _get_db_version():
    """直接用 raw SQL 读取 cache_version，避免 ORM 开销和循环依赖。"""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT JSON_EXTRACT(config, '$.cache_version') "
                "FROM aivision_platform_config "
                "WHERE platform_code = 'system' AND status = 1 "
                "LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                return _coerce_version(row[0])
    except Exception:
        logger.debug("Failed to read cache_version from DB", exc_info=True)
    return 0


def _bump_db_version():
    """原子递增 DB 中的 cache_version。"""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE aivision_platform_config "
                "SET config = JSON_SET(COALESCE(config, '{}'), '$.cache_version', "
                "  COALESCE(JSON_EXTRACT(config, '$.cache_version'), 0) + 1"
                ") "
                "WHERE platform_code = 'system' AND status = 1"
            )
    except Exception:
        logger.warning("Failed to bump cache_version in DB", exc_info=True)


def _check_version():
    """检查 DB version，如果变了就清空本地全部缓存。节流：每 _VERSION_CHECK_INTERVAL 秒最多查一次。"""
    global _local_version, _last_version_check

    now = time.monotonic()
    if now - _last_version_check < _VERSION_CHECK_INTERVAL:
        return
    _last_version_check = now

    db_version = _get_db_version()
    if db_version != _local_version:
        logger.info(
            "Cache version changed: local=%d, db=%d — flushing local cache",
            _local_version,
            db_version,
        )
        _flush_all_local_cache()
        _local_version = db_version


def _flush_all_local_cache():
    """清空 LocMemCache 中所有业务 key（按已知前缀）。"""
    for prefixes in MODEL_CACHE_PREFIXES.values():
        for prefix in prefixes:
            _delete_keys_by_prefix(prefix)
    # 同时清掉 TTL 配置缓存
    cache.delete(_CACHE_TTL_KEY)


def cached_query(key, queryset_fn, timeout=None):
    """从缓存取数据，未命中则执行 queryset_fn 并写入缓存。
    timeout 为 None 时自动从 DB 读取配置的 TTL。
    每次调用时顺带检查跨进程 version。"""
    _check_version()

    result = cache.get(key)
    if result is not None:
        return result
    result = queryset_fn()
    if timeout is None:
        timeout = _get_cache_ttl()
    cache.set(key, result, timeout)
    return result


def _get_locmem_backend():
    """获取真实的 LocMemCache 后端实例，非 LocMemCache 时返回 None。"""
    real_cache = caches["default"]
    return real_cache if isinstance(real_cache, LocMemCache) else None


def _delete_keys_by_prefix(prefix):
    """按前缀删除 LocMemCache 中的 key。"""
    real_cache = _get_locmem_backend()
    if not real_cache:
        return

    # LocMemCache 内部 key 会带版本前缀，如 ":1:agent_types:active"
    keys_to_delete = []
    with real_cache._lock:
        for internal_key in list(real_cache._cache.keys()):
            if f":{prefix}" in internal_key:
                keys_to_delete.append(internal_key)

    for k in keys_to_delete:
        try:
            del real_cache._cache[k]
            real_cache._expire_info.pop(k, None)
        except KeyError:
            pass

    if keys_to_delete:
        logger.debug(
            "Cache invalidated: prefix=%s, keys=%d", prefix, len(keys_to_delete)
        )


def _invalidate_model_cache(sender, **kwargs):
    """post_save / post_delete signal handler，清除本进程缓存并 bump DB version。"""
    global _local_version

    model_name = sender.__name__
    prefixes = MODEL_CACHE_PREFIXES.get(model_name, [])
    for prefix in prefixes:
        _delete_keys_by_prefix(prefix)

    # bump DB version，让其他进程感知到变更
    _bump_db_version()
    # 同步本进程的 local_version，避免自己下次轮询时重复 flush
    _local_version = _get_db_version()


def get_cache_info():
    """返回当前缓存状态信息，用于诊断接口。"""
    ttl = _get_cache_ttl()
    real_cache = _get_locmem_backend()

    keys = []
    if real_cache:
        with real_cache._lock:
            for internal_key in real_cache._cache.keys():
                # 去掉版本前缀 ":1:xxx" → "xxx"
                parts = internal_key.split(":", 2)
                actual_key = parts[2] if len(parts) >= 3 else internal_key
                keys.append(actual_key)

    return {
        "backend": "LocMemCache",
        "cache_ttl": ttl,
        "cache_ttl_unit": "seconds",
        "ttl_source": "PlatformConfig(platform_code='system').config.cache_ttl",
        "ttl_refresh_interval": f"{_CACHE_TTL_SELF_TIMEOUT}s",
        "cached_keys": sorted(keys),
        "cached_key_count": len(keys),
        "signal_watched_models": list(MODEL_CACHE_PREFIXES.keys()),
        "cache_version_local": _local_version,
        "cache_version_check_interval": f"{_VERSION_CHECK_INTERVAL}s",
    }


def connect_cache_signals():
    """在 AppConfig.ready() 中调用，注册 signal。"""
    from .models import AgentTypeConfig, DataSource, AgentDashboardPanel

    for model in (AgentTypeConfig, DataSource, AgentDashboardPanel):
        post_save.connect(_invalidate_model_cache, sender=model)
        post_delete.connect(_invalidate_model_cache, sender=model)

    # 启动时读一次 DB version
    global _local_version
    _local_version = _get_db_version()

    logger.info(
        "Cache signals connected for: %s (initial version=%d)",
        list(MODEL_CACHE_PREFIXES.keys()),
        _local_version,
    )
