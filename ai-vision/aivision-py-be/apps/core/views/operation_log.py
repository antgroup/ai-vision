"""空间操作审计日志视图"""

import logging
from datetime import datetime

from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Space, SpaceOperationLog
from ..serializers import SpaceOperationLogSerializer
from .common import (
    StandardPagination,
    check_space_access_or_error,
    error_response,
)

logger = logging.getLogger(__name__)


HIDDEN_CATEGORIES = {SpaceOperationLog.CATEGORY_TRACE_DESENSITIZE_VIEW}

CATEGORY_OPTIONS = [
    {"value": value, "label": label}
    for value, label in SpaceOperationLog.CATEGORY_CHOICES
    if value not in HIDDEN_CATEGORIES
]
_VALID_CATEGORIES = {
    value for value, _ in SpaceOperationLog.CATEGORY_CHOICES
} - HIDDEN_CATEGORIES

_KEYWORD_MAX_LEN = 100


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class SpaceOperationLogListView(APIView):
    """GET /api/v1/spaces/{space_id}/operation-logs"""

    def get(self, request, space_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        queryset = (
            SpaceOperationLog.objects.filter(space=space)
            .select_related("operator")
            .order_by("-gmt_create", "-id")
        )

        category = request.query_params.get("category")
        if category and category != "all":
            if category not in _VALID_CATEGORIES:
                return error_response("Invalid category", 4002)
            queryset = queryset.filter(category=category)

        keyword = (request.query_params.get("keyword") or "").strip()[
            :_KEYWORD_MAX_LEN
        ]
        if keyword:
            queryset = queryset.filter(
                Q(target_display__icontains=keyword)
                | Q(detail__icontains=keyword)
            )

        from_dt = _parse_iso_datetime(request.query_params.get("from"))
        to_dt = _parse_iso_datetime(request.query_params.get("to"))
        if from_dt:
            queryset = queryset.filter(gmt_create__gte=from_dt)
        if to_dt:
            queryset = queryset.filter(gmt_create__lte=to_dt)

        total = queryset.count()
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = SpaceOperationLogSerializer(page, many=True)

        return Response(
            {
                "code": 0,
                "message": "success",
                "data": {
                    "items": serializer.data,
                    "categories": CATEGORY_OPTIONS,
                    "pagination": {
                        "page": paginator.page.number,
                        "pageSize": paginator.page_size,
                        "total": total,
                        "totalPages": paginator.page.paginator.num_pages,
                    },
                },
            }
        )
