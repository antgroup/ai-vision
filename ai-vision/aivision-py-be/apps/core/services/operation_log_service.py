"""空间操作审计日志写入服务

提供两个能力：

1. `log_operation(...)` — 底层写入函数，直接落库一条 `SpaceOperationLog`，
   任何调用方都可以直接使用。
2. `@log_op(category, action)` — view 方法装饰器，在原方法返回成功响应
   （HTTP < 400 且 `response.data["code"] == 0`）后，自动从 `request.op_log`
   读取动态字段写入审计日志。

设计约束：
- 日志写入失败不影响业务主流程，只 `logger.warning`。
- 未认证用户、解析不到空间时直接跳过写入。
- 装饰器只追加新行为，不修改 view 的返回值。
"""

import logging
from functools import wraps
from typing import Any

from ..models import Space, SpaceOperationLog

logger = logging.getLogger(__name__)

_OP_LOG_REQUEST_ATTR = "op_log"


def log_operation(
    *,
    space,
    operator,
    category: str,
    action: str,
    target_display: str = "",
    detail: str = "",
    extra: dict | None = None,
) -> SpaceOperationLog | None:
    """直接写入一条操作日志。失败不抛异常，返回 None。"""
    if space is None or operator is None or not operator.is_authenticated:
        return None
    try:
        return SpaceOperationLog.objects.create(
            space=space,
            operator=operator,
            category=category,
            action=action,
            target_display=target_display or "",
            detail=detail or "",
            extra=extra,
        )
    except Exception:
        logger.warning(
            "op_log write failed: space=%s operator=%s category=%s action=%s",
            getattr(space, "id", None),
            getattr(operator, "id", None),
            category,
            action,
            exc_info=True,
        )
        return None


def _is_success_response(response) -> bool:
    """项目内成功统一定义：HTTP < 400 且 业务 code == 0。"""
    if response is None:
        return False
    status_code = getattr(response, "status_code", 200)
    if status_code >= 400:
        return False
    data = getattr(response, "data", None)
    if not isinstance(data, dict):
        # 非标准响应（如 streaming/file），按 HTTP 判
        return True
    return data.get("code") == 0


def _resolve_space(space_id: Any) -> Space | None:
    if space_id is None:
        return None
    try:
        return Space.objects.filter(uuid=str(space_id), status=1).first()
    except Exception:
        logger.warning("op_log resolve space failed: space_id=%s", space_id, exc_info=True)
        return None


def _normalize_payloads(raw) -> list[dict]:
    if raw is None:
        return [{}]
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [item for item in raw if isinstance(item, dict)] or []
    return []


def log_op(
    *,
    category: str,
    action: str,
    space_id_kwarg: str = "space_id",
):
    """View 方法装饰器：成功后自动写入一条或多条审计日志。

    动态字段（target_display / detail / extra）通过 `request.op_log` 传递，
    支持 dict 或 list[dict]：
    - dict：写入一条
    - list[dict]：每个元素写入一条（用于批量场景，如批量添加成员）
    - 未设置：仅以 category/action 写入一条
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(self, request, *args, **kwargs):
            response = view_func(self, request, *args, **kwargs)

            try:
                if not _is_success_response(response):
                    return response

                user = getattr(request, "user", None)
                if user is None or not getattr(user, "is_authenticated", False):
                    return response

                space_id = kwargs.get(space_id_kwarg)
                space = _resolve_space(space_id)
                if space is None:
                    return response

                payloads = _normalize_payloads(
                    getattr(request, _OP_LOG_REQUEST_ATTR, None)
                )
                for payload in payloads:
                    log_operation(
                        space=space,
                        operator=user,
                        category=category,
                        action=action,
                        target_display=payload.get("target_display", ""),
                        detail=payload.get("detail", ""),
                        extra=payload.get("extra"),
                    )
            except Exception:
                logger.warning(
                    "op_log decorator failed: category=%s action=%s",
                    category,
                    action,
                    exc_info=True,
                )

            return response

        return wrapper

    return decorator
