"""用户相关视图"""

from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from ..models import Space, User
from ..serializers import SpaceSerializer, UserSerializer
from .common import error_response, success_response


class HealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return success_response({"status": "ok", "service": "aivision-backend"})


class CacheInfoView(APIView):
    def get(self, request):
        from ..cache import get_cache_info

        return success_response(get_cache_info())


class CurrentUserView(APIView):
    def get(self, request):
        data = UserSerializer(request.user).data
        data["product_guide_pending"] = request.user.product_guide_pending
        personal_space = Space.objects.filter(
            type=1,
            status=1,
            gmt_deleted__isnull=True,
            members__user=request.user,
            members__gmt_deleted__isnull=True,
        ).first()
        data["personal_space"] = (
            SpaceSerializer(personal_space).data if personal_space else None
        )
        return success_response(data)

    def patch(self, request):
        payload = request.data
        if (
            not isinstance(payload, dict)
            or len(payload) != 1
            or payload.get("product_guide_pending") is not False
        ):
            return error_response("Expected product_guide_pending to be false")

        User.objects.filter(pk=request.user.pk, product_guide_pending=True).update(
            product_guide_pending=False, gmt_modified=timezone.now()
        )
        return success_response({"product_guide_pending": False})
