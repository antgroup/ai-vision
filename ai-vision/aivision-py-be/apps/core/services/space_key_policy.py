"""集中策略：SpaceKey 创建与限额。

将原本散落于 model/view/auth 三处的空间类型限制收敛到单一策略点，
便于未来按空间类型差异化收紧，并提供 per-space 数量上限防滥用。
"""

from functools import wraps

from django.db import DatabaseError, transaction

from ..models import Space, SpaceKey

# 单空间最多持有的外部消费 API Key 数量（type 无关，仅在 POST 创建路径强制）。
MAX_KEYS_PER_SPACE = 10
_OCEANBASE_SERIALIZATION_FAILURE = 6235


def can_create_for(space) -> bool:
    """是否允许在该空间创建 SpaceKey。当前对所有空间类型放行。"""
    return True


def atomic_space_key_creation(view):
    @wraps(view)
    def run(*args, **kwargs):
        for attempt in range(2):
            try:
                with transaction.atomic():
                    return view(*args, **kwargs)
            except DatabaseError as exc:
                # OceanBase REPEATABLE READ can abort a waiter when its
                # snapshot predates the lock holder. Retry after rollback.
                if attempt or exc.args[:1] != (_OCEANBASE_SERIALIZATION_FAILURE,):
                    raise

    return run


def lock_space_key_creation(space) -> None:
    """Serialize both key creation paths until the caller's transaction ends."""
    Space.objects.select_for_update().only("pk").get(pk=space.pk)


def key_count_exceeded(space) -> bool:
    """Check the quota after locking the Space in the creation transaction."""
    # Authorization may already have established an older REPEATABLE READ
    # snapshot. Lock and materialize rows so this check reads current data.
    key_ids = (
        SpaceKey.objects.select_for_update()
        .filter(space=space)
        .order_by("pk")
        .values_list("pk", flat=True)[:MAX_KEYS_PER_SPACE]
    )
    return len(key_ids) >= MAX_KEYS_PER_SPACE
