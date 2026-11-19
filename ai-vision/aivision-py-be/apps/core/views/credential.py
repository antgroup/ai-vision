"""凭证管理视图"""

import logging

from rest_framework.views import APIView

from ..models import Space, SpaceMember, SpaceOperationLog
from ..services.operation_log_service import log_op
from .common import success_response, error_response, check_space_access_or_error

logger = logging.getLogger(__name__)


_KEY_SCOPE_LABELS = {"WRITE": "上报 Key", "READ": "读取 Key"}


def _scope_label(scope: str | None) -> str:
    return _KEY_SCOPE_LABELS.get((scope or "").upper(), scope or "Key")


_REVEAL_INTENT_COPY = "copy"


class CredentialRevealView(APIView):
    """取消脱敏查看完整 Key（仅管理员）"""

    @log_op(
        category=SpaceOperationLog.CATEGORY_KEY_OPERATION,
        action="reveal_key",
    )
    def post(self, request, space_id, credential_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        if not SpaceMember.objects.filter(
            space=space, user=request.user, role=1, gmt_deleted__isnull=True
        ).exists():
            return error_response("仅管理员可查看凭证明文", 3020, status=403)

        from apps.core.models import SpaceObservationCredential
        from apps.core.services.credential_service import reveal_credential

        try:
            credential = SpaceObservationCredential.objects.get(
                id=credential_id,
                space_observation_project__space=space,
                status=1,
                gmt_deleted__isnull=True,
            )
        except SpaceObservationCredential.DoesNotExist:
            return error_response("Credential not found", 3021)

        result = reveal_credential(credential)
        intent = (request.query_params.get("intent") or "").strip().lower()
        is_copy = intent == _REVEAL_INTENT_COPY
        request.op_log = {
            "target_display": _scope_label(credential.scope),
            "detail": "复制 Key" if is_copy else "查看明文",
        }
        return success_response(result)


class CredentialRegenerateView(APIView):
    """重新生成 Key（仅管理员）"""

    @log_op(
        category=SpaceOperationLog.CATEGORY_KEY_OPERATION,
        action="regenerate_key",
    )
    def post(self, request, space_id, credential_id):
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001)

        access_error = check_space_access_or_error(request.user, space)
        if access_error:
            return access_error

        if not SpaceMember.objects.filter(
            space=space, user=request.user, role=1, gmt_deleted__isnull=True
        ).exists():
            return error_response("仅管理员可重新生成凭证", 3020, status=403)

        from apps.core.models import SpaceObservationCredential
        from apps.core.services.credential_service import regenerate_credential
        from apps.core.services.credential_provider import LangfuseCredentialProvider

        try:
            credential = SpaceObservationCredential.objects.select_related(
                "space_observation_project__endpoint_config"
            ).get(
                id=credential_id,
                space_observation_project__space=space,
                status=1,
                gmt_deleted__isnull=True,
            )
        except SpaceObservationCredential.DoesNotExist:
            return error_response("Credential not found", 3021)

        project = credential.space_observation_project
        provider = None
        if project.upstream_project_id:
            endpoint = project.endpoint_config
            provider = LangfuseCredentialProvider(
                base_url=endpoint.base_url,
                admin_public_key=endpoint.admin_public_key or "",
                admin_secret_ciphertext=endpoint.admin_secret_ciphertext or "",
            )

        try:
            result = regenerate_credential(credential, provider)
            request.op_log = {
                "target_display": _scope_label(credential.scope),
                "detail": "重新生成 Key",
            }
            return success_response(result)
        except Exception as exc:
            logger.warning(
                "Credential regeneration failed (credential_id=%s, error=%s)",
                credential.id,
                type(exc).__name__,
            )
            return error_response("无法重新生成凭证，请检查观测服务配置后重试", 3022)
