"""Agent 管理相关视图"""

import logging

from cryptography.fernet import InvalidToken
from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import (
    Agent,
    AgentTypeConfig,
    ApiKey,
    Space,
    SpaceKey,
    SpaceOperationLog,
)
from ..permissions.constants import (
    AGENT_INGEST_TOKEN_MINT,
    AGENT_MANAGE,
    AGENT_READ,
    SPACE_KEY_MANAGE,
)
from ..permissions.space import require_space_permission
from ..serializers import (
    AgentSerializer,
    AgentTypeConfigSerializer,
    CreateAgentSerializer,
)
from ..services.credential_service import project_onboarding_credential_cipher
from ..services.observation_subject_service import require_default_subject
from ..services.onboarding_demo import DEMO_METADATA_KEY
from ..services.operation_log_service import log_op
from ..services.personal_diagnosis import sync_personal_diagnosis_after_commit
from ..services.plugin_artifacts import market_display_scope, preview_plugin_available
from ..services.space_key_policy import (
    atomic_space_key_creation,
    key_count_exceeded,
    lock_space_key_creation,
)
from .common import (
    StandardPagination,
    error_response,
    success_response,
)

logger = logging.getLogger(__name__)


def _initial_agent_status(agent_type_config):
    if agent_type_config and (
        _is_self_built_integration(agent_type_config)
        or agent_type_config.setup_mode != "AUTO"
    ):
        return 2
    return 1


def _is_market_plugin(config):
    return bool(
        config
        and config.setup_mode == "CLI"
        and (config.config_guide or {}).get("plugin_key")
    )


def _is_manual_plugin_guide(config):
    if not config or config.category != "CUSTOM" or config.agent_name == "CUSTOM":
        return False
    guide = config.config_guide
    if not (
        config.setup_mode == "OTHER"
        and isinstance(guide, dict)
        and guide.get("schema") == "v2"
        and isinstance(guide.get("sections"), list)
    ):
        return False
    native = guide.get("native_relay_onboarding")
    if config.agent_product == "Hermes":
        return bool(
            isinstance(native, dict)
            and native.get("enabled") is True
            and isinstance(native.get("integrations"), list)
            and "hermes-relay" in native["integrations"]
        )
    return bool(guide["sections"])


def _is_self_built_integration(config):
    if not config:
        return False
    guide = config.config_guide
    integration = guide.get("integration") if isinstance(guide, dict) else None
    if isinstance(integration, dict) and integration.get("kind") in {
        "observability_sdk",
        "partner_runtime",
    }:
        return True
    # Preserve project lifecycle for catalogs created before integration metadata.
    legacy_products = {"langfuse", "nvidia-nemo-relay"}
    return any(
        (value or "").lower() in legacy_products
        for value in (config.agent_product, config.agent_name)
    )


def _is_market_origin(config):
    """Market plugins and self-built projects are user-managed entries."""
    return bool(config) and (
        _is_market_plugin(config)
        or _is_manual_plugin_guide(config)
        or _is_self_built_integration(config)
    )


class AgentTypeListView(APIView):
    def get(self, request):
        from ..cache import cached_query

        agent_types = cached_query(
            "agent_types:active",
            lambda: list(
                AgentTypeConfig.objects.active_shared_runtime().order_by("sort_order")
            ),
        )
        serializer = AgentTypeConfigSerializer(agent_types, many=True)
        return success_response(
            [
                {
                    **agent_type,
                    "display_scope": market_display_scope(
                        agent_type["display_scope"], agent_type["config_guide"]
                    ),
                }
                for agent_type in serializer.data
            ]
        )


class AgentListView(APIView):
    def get(self, request):
        space_id = request.query_params.get("space_id")
        user = request.user

        agents = Agent.objects.select_related("agent_type").filter(status=1)
        if space_id:
            ctx = require_space_permission(
                user=user, space_id=space_id, permission=AGENT_READ
            )
            agents = agents.filter(space=ctx.space)
        elif user:
            agents = agents.filter(
                space__status=1,
                space__gmt_deleted__isnull=True,
                space__members__user=user,
                space__members__gmt_deleted__isnull=True,
            )

        paginator = StandardPagination()
        page = paginator.paginate_queryset(agents.distinct(), request)
        serializer = AgentSerializer(page, many=True)

        return Response(
            {
                "code": 0,
                "message": "success",
                "data": {
                    "items": serializer.data,
                    "pagination": {
                        "page": paginator.page.number,
                        "pageSize": paginator.page_size,
                        "total": agents.count(),
                        "totalPages": paginator.page.paginator.num_pages,
                    },
                },
            }
        )


class ApiKeyListView(APIView):
    def get(self, request):
        user = request.user

        api_keys = ApiKey.objects.filter(user=user, gmt_deleted__isnull=True)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(api_keys, request)
        from ..serializers import ApiKeySerializer

        serializer = ApiKeySerializer(page, many=True)

        return Response(
            {
                "code": 0,
                "message": "success",
                "data": {
                    "items": serializer.data,
                    "pagination": {
                        "page": paginator.page.number,
                        "pageSize": paginator.page_size,
                        "total": api_keys.count(),
                        "totalPages": paginator.page.paginator.num_pages,
                    },
                },
            }
        )


class SpaceAgentsView(APIView):
    def get(self, request, space_id):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=AGENT_READ
        )

        agents = (
            Agent.objects.select_related("agent_type")
            .filter(space=ctx.space, status__in=[1, 2])
            .order_by("-gmt_create")
        )

        from ..cache import cached_query

        all_configs = cached_query(
            "agent_types:shared_runtime",
            lambda: list(AgentTypeConfig.objects.shared_runtime()),
        )
        config_by_id = {atc.id: atc for atc in all_configs}
        config_by_name = {atc.agent_name: atc for atc in all_configs}
        config_by_product = {atc.agent_product: atc for atc in all_configs}

        result = []
        for agent in agents:
            config = (
                agent.agent_type
                or config_by_id.get(agent.agent_type_id)
                or config_by_product.get(agent.agent_product)
                or config_by_name.get(agent.agent_product)
            )
            result.append(
                {
                    "agent_uuid": agent.uuid,
                    "agent_name": config.agent_name if config else agent.agent_product,
                    "agent_product": (
                        config.agent_product if config else agent.agent_product
                    ),
                    "agent_type_id": agent.agent_type_id,
                    "runtime_type": config.runtime_type if config else "",
                    "display_name": config.display_name if config else None,
                    "display_scope": config.display_scope if config else None,
                    "name": agent.name,
                    "custom_name": agent.custom_name,
                    "metadata": agent.metadata,
                    "description": config.description if config else None,
                    "status": agent.status,
                    "last_active_at": (
                        agent.last_active_at.isoformat()
                        if agent.last_active_at
                        else None
                    ),
                }
            )

        return success_response(result)


class SpaceAgentsCreateView(APIView):
    @transaction.atomic
    @log_op(
        category=SpaceOperationLog.CATEGORY_AGENT_ACCESS,
        action="create_agent",
    )
    def post(self, request, space_id):
        serializer = CreateAgentSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(serializer.errors, 1002)

        agent_product = serializer.validated_data.get("agent_product")
        name = (serializer.validated_data.get("name") or "").strip()
        instance_id = serializer.validated_data.get("instance_id")
        metadata = serializer.validated_data.get("metadata")

        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=AGENT_MANAGE
        )
        space = ctx.space

        # Serialize registrations in a space: repeated clicks and multiple members
        # must receive the same market Agent instead of creating duplicates.
        Space.objects.select_for_update().get(pk=space.pk)

        agent_type_config = (
            AgentTypeConfig.objects.active_shared_runtime()
            .filter(
                agent_product=agent_product,
            )
            .first()
            or AgentTypeConfig.objects.active_shared_runtime()
            .filter(
                agent_name=agent_product,
            )
            .first()
        )
        if not agent_type_config or not preview_plugin_available(agent_type_config.config_guide):
            return error_response(f"Agent type not found: {agent_product}", 1006)

        # SDK/runtime integrations create distinct business projects; installed
        # Agent plugins keep one resumable registration per product and space.
        if _is_self_built_integration(agent_type_config):
            if not name:
                return error_response("项目名称不能为空", 1002)
        else:
            name = name or agent_type_config.agent_product

        existing_filters = {
            "space": space,
            "agent_product": agent_type_config.agent_product,
            "gmt_deleted__isnull": True,
        }
        existing_agent = Agent.objects.filter(**existing_filters).first()
        if existing_agent and not _is_self_built_integration(agent_type_config):
            if existing_agent.status == 0:
                existing_agent.status = _initial_agent_status(agent_type_config)
                existing_agent.save(update_fields=["status", "gmt_modified"])
                sync_personal_diagnosis_after_commit(existing_agent)
                return success_response(AgentSerializer(existing_agent).data)
            if _is_market_plugin(agent_type_config):
                # Plugin registration is resumable: re-running the guide dialog
                # must reuse the existing management record.
                return success_response(AgentSerializer(existing_agent).data)
            return error_response("Agent already exists in this space", 1005)

        initial_status = _initial_agent_status(agent_type_config)
        subject_id = require_default_subject(space=space, user=request.user)
        binding = request.user.observation_subject_bindings.get(
            space=space,
            source_system="collector",
            subject_id=subject_id,
            status=1,
            gmt_deleted__isnull=True,
        )

        agent = Agent.objects.create(
            space=space,
            agent_type=agent_type_config,
            agent_product=agent_type_config.agent_product,
            name=name,
            custom_name=name,
            instance_id=instance_id,
            metadata={k: v for k, v in metadata.items() if k != DEMO_METADATA_KEY}
            if isinstance(metadata, dict)
            else {},
            observation_subject_binding=binding,
            status=initial_status,
        )

        display_name = (
            agent_type_config.display_name or agent_type_config.agent_product or name
        )
        request.op_log = {
            "target_display": display_name,
            "detail": "新增 Agent 接入",
        }

        sync_personal_diagnosis_after_commit(agent)
        return success_response(AgentSerializer(agent).data)


class SpaceAgentDisableView(APIView):
    """Remove an Agent, including market plugins in team spaces."""

    @log_op(category=SpaceOperationLog.CATEGORY_AGENT_ACCESS, action="disable_agent")
    @transaction.atomic
    def post(self, request, space_id, agent_uuid):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=AGENT_MANAGE
        )
        space = ctx.space

        try:
            agent = Agent.objects.select_for_update().get(
                uuid=agent_uuid, space=space, gmt_deleted__isnull=True
            )
        except Agent.DoesNotExist:
            return error_response("Agent not found", 1006)

        if space.type != 1 and not _is_market_origin(agent.agent_type):
            return error_response("团队空间仅支持移除市场接入的 Agent", 1010)

        if agent.status == 0:
            return error_response("Agent 已处于移除状态", 1011)

        agent.status = 0
        agent.save(update_fields=["status", "gmt_modified"])
        agent.installations.filter(enabled=True).update(enabled=False)

        config = agent.agent_type
        display_name = (config.display_name if config else None) or agent.agent_product
        request.op_log = {"target_display": display_name, "detail": "从频道移除 Agent"}

        sync_personal_diagnosis_after_commit(agent)
        return success_response({"agent_uuid": agent.uuid, "status": agent.status})


class ProjectOnboardingCredentialView(APIView):
    """Return one reusable credential for the selected self-built project."""

    @log_op(category=SpaceOperationLog.CATEGORY_KEY_OPERATION, action="reveal_key")
    @atomic_space_key_creation
    def post(self, request, space_id, agent_uuid):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=AGENT_INGEST_TOKEN_MINT
        )
        lock_space_key_creation(ctx.space)
        agent = (
            Agent.objects.select_for_update()
            .filter(
                space=ctx.space,
                uuid=agent_uuid,
                status__in=[1, 2],
                gmt_deleted__isnull=True,
            )
            .first()
        )
        if agent is None or not _is_self_built_integration(agent.agent_type):
            return error_response("Project not found", 1006, status=404)

        key = (
            SpaceKey.objects.select_for_update()
            .filter(space=ctx.space, onboarding_agent=agent)
            .first()
        )
        if key is not None:
            if (
                key.creator_id != request.user.pk
                and SPACE_KEY_MANAGE not in ctx.permissions
            ):
                return error_response(
                    "Project credential access denied", 3020, status=403
                )
            if not key.enabled:
                return error_response("Project credential is disabled", 3032, status=409)
        elif key_count_exceeded(ctx.space):
            return error_response("API Key 数量已达上限", 3033, status=409)

        cipher = project_onboarding_credential_cipher()
        if cipher is None:
            return error_response(
                "Configure a private CREDENTIAL_ENCRYPTION_KEY or SECRET_KEY "
                "to use project credentials",
                5032,
                status=503,
            )

        if key is not None:
            try:
                ciphertext = (key.onboarding_ciphertext or "").encode()
                raw_key = cipher.decrypt(ciphertext).decode()
            except (InvalidToken, UnicodeDecodeError):
                return error_response(
                    "Project credential is unavailable", 3032, status=409
                )
        else:
            raw_key, prefix, key_hash = SpaceKey.generate_key()
            SpaceKey.objects.create(
                space=ctx.space,
                onboarding_agent=agent,
                onboarding_ciphertext=cipher.encrypt(raw_key.encode()).decode(),
                key_prefix=prefix,
                key_hash=key_hash,
                name=agent.custom_name or agent.name,
                creator=request.user,
            )

        request.op_log = {
            "target_display": agent.custom_name or agent.name,
            "detail": "查看项目凭证明文" if key is not None else "创建并查看项目凭证",
        }
        response = success_response({"key": raw_key})
        response["Cache-Control"] = "private, no-store"
        return response


class SpaceAgentMarkConnectedView(APIView):
    """Promote a pending Agent after the console verifies real trace data."""

    @log_op(category=SpaceOperationLog.CATEGORY_AGENT_ACCESS, action="mark_connected")
    @transaction.atomic
    def post(self, request, space_id, agent_uuid):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=AGENT_MANAGE
        )
        metadata = request.data.get("metadata")
        if isinstance(metadata, dict):
            metadata = {k: v for k, v in metadata.items() if k != DEMO_METADATA_KEY}
        agent = (
            Agent.objects.select_for_update()
            .select_related("agent_type")
            .filter(
                uuid=agent_uuid,
                space=ctx.space,
                gmt_deleted__isnull=True,
            )
            .first()
        )
        if agent is None:
            return error_response("Agent not found", 1006)

        if not _is_self_built_integration(agent.agent_type):
            return error_response("Agent is not a self-built integration project", 1012)

        if agent.status == 1:
            if isinstance(metadata, dict) and metadata:
                agent.metadata = {**agent.metadata, **metadata}
                agent.save(update_fields=["metadata", "gmt_modified"])
                sync_personal_diagnosis_after_commit(agent)
            return success_response(
                {"agent_uuid": agent.uuid, "status": agent.status}
            )

        if agent.status != 2:
            return error_response("Agent is not pending connection", 1013)

        agent.status = 1
        if isinstance(metadata, dict):
            agent.metadata = {**agent.metadata, **metadata}
            agent.save(update_fields=["metadata", "status", "gmt_modified"])
        else:
            agent.save(update_fields=["status", "gmt_modified"])
        sync_personal_diagnosis_after_commit(agent)

        return success_response({"agent_uuid": agent.uuid, "status": agent.status})


class SpaceAgentEnableView(APIView):
    """Restore a removed Agent; manual plugins still require installation."""

    @log_op(category=SpaceOperationLog.CATEGORY_AGENT_ACCESS, action="enable_agent")
    def post(self, request, space_id, agent_uuid):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=AGENT_MANAGE
        )
        space = ctx.space

        try:
            agent = Agent.objects.get(
                uuid=agent_uuid,
                space=space,
                status=0,
                gmt_deleted__isnull=True,
            )
        except Agent.DoesNotExist:
            return error_response("Agent not found or not removed", 1006)

        if space.type != 1 and not _is_market_origin(agent.agent_type):
            return error_response("团队空间仅支持加回市场接入的 Agent", 1010)

        config = (
            agent.agent_type
            or AgentTypeConfig.objects.active_shared_runtime()
            .filter(
                agent_product=agent.agent_product,
            )
            .first()
            or AgentTypeConfig.objects.active_shared_runtime()
            .filter(
                agent_name=agent.agent_product,
            )
            .first()
        )
        agent.status = _initial_agent_status(config)
        agent.save(update_fields=["status", "gmt_modified"])

        display_name = (config.display_name if config else None) or agent.agent_product
        request.op_log = {"target_display": display_name, "detail": "加回频道 Agent"}

        sync_personal_diagnosis_after_commit(agent)
        return success_response({"agent_uuid": agent.uuid, "status": agent.status})
