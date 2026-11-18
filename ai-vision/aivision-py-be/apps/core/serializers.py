from django.utils import timezone as dj_timezone
from rest_framework import serializers

from .models import (
    Agent,
    AgentTypeConfig,
    ApiKey,
    Session,
    Space,
    SpaceKey,
    SpaceMember,
    SpaceOperationLog,
    User,
)


class UserSerializer(serializers.ModelSerializer):
    user_uuid = serializers.CharField(source="uuid", read_only=True)

    class Meta:
        model = User
        fields = [
            "user_uuid",
            "name",
            "email",
            "avatar_url",
            "status",
            "gmt_create",
        ]


class TokenResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()


class SpaceSerializer(serializers.ModelSerializer):
    owner_uuid = serializers.CharField(
        source="owner.uuid", read_only=True, allow_null=True
    )
    space_id = serializers.UUIDField(source="uuid", read_only=True)
    creator_name = serializers.CharField(source="creator.name", read_only=True)
    member_count = serializers.SerializerMethodField()
    agent_count = serializers.SerializerMethodField()

    class Meta:
        model = Space
        fields = [
            "space_id",
            "name",
            "type",
            "scope",
            "description",
            "creator_name",
            "owner_uuid",
            "member_count",
            "agent_count",
            "status",
            "gmt_create",
        ]

    def get_member_count(self, obj):
        return obj.members.filter(gmt_deleted__isnull=True).count()

    def get_agent_count(self, obj):
        return obj.agents.filter(gmt_deleted__isnull=True).count()


class SpaceMemberSerializer(serializers.ModelSerializer):
    is_owner = serializers.SerializerMethodField()

    def get_is_owner(self, obj):
        return obj.space.type == 2 and obj.space.owner_id == obj.user_id

    user_uuid = serializers.CharField(source="user.uuid", read_only=True)
    name = serializers.CharField(source="user.name", read_only=True)
    email = serializers.CharField(source="user.normalized_email", read_only=True)
    avatar_url = serializers.CharField(
        source="user.avatar_url", read_only=True, allow_null=True
    )
    account_status = serializers.SerializerMethodField()

    def get_account_status(self, obj):
        return "pending" if obj.user.status == User.STATUS_PENDING else "active"

    class Meta:
        model = SpaceMember
        fields = [
            "user_uuid",
            "name",
            "email",
            "avatar_url",
            "account_status",
            "is_owner",
            "role",
            "joined_at",
        ]


class AgentTypeConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentTypeConfig
        fields = [
            "id",
            "agent_name",
            "agent_product",
            "runtime_type",
            "display_name",
            "display_scope",
            "category",
            "setup_mode",
            "config_guide",
            "icon",
            "description",
            "cli_command",
            "sort_order",
            "capabilities",
            "status",
        ]


class AgentSerializer(serializers.ModelSerializer):
    agent_uuid = serializers.CharField(source="uuid", read_only=True)
    description = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            "agent_uuid",
            "api_key_id",
            "agent_type_id",
            "agent_product",
            "name",
            "custom_name",
            "version",
            "instance_id",
            "metadata",
            "status",
            "last_active_at",
            "gmt_create",
            "description",
        ]

    def get_description(self, obj):
        if getattr(obj, "agent_type", None):
            return obj.agent_type.description
        config = (
            AgentTypeConfig.objects.shared_runtime()
            .filter(
                agent_product=obj.agent_product,
            )
            .first()
            or AgentTypeConfig.objects.shared_runtime()
            .filter(
                agent_name=obj.agent_product,
            )
            .first()
        )
        return config.description if config else None


class ApiKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiKey
        fields = [
            "id",
            "key_prefix",
            "name",
            "source_platform",
            "space_id",
            "langfuse_project_id",
            "langfuse_project_name",
            "langfuse_mapping_status",
            "status",
            "expires_at",
            "last_used_at",
            "gmt_create",
        ]


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = [
            "id",
            "agent_id",
            "trace_id",
            "status",
            "tokens_input",
            "tokens_output",
            "error_type",
            "error_message",
            "gmt_create",
        ]


class CreateAgentSerializer(serializers.Serializer):
    agent_product = serializers.CharField(max_length=128)
    name = serializers.CharField(max_length=128, required=False, allow_null=True)
    instance_id = serializers.CharField(max_length=128, required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, allow_null=True)


class SpaceOperationLogSerializer(serializers.ModelSerializer):
    operator_name = serializers.CharField(source="operator.name", read_only=True)
    operator_uuid = serializers.CharField(source="operator.uuid", read_only=True)
    operator_avatar = serializers.CharField(
        source="operator.avatar_url", read_only=True, allow_null=True
    )
    category_display = serializers.CharField(
        source="get_category_display", read_only=True
    )
    time = serializers.SerializerMethodField()

    class Meta:
        model = SpaceOperationLog
        fields = [
            "id",
            "time",
            "operator_name",
            "operator_uuid",
            "operator_avatar",
            "category",
            "category_display",
            "action",
            "target_display",
            "detail",
        ]

    def get_time(self, obj):
        if obj.gmt_create is None:
            return None
        local = dj_timezone.localtime(obj.gmt_create)
        return local.strftime("%Y-%m-%d %H:%M:%S")


class SpaceKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=128, required=False, default="", allow_blank=True
    )


class SpaceKeyListSerializer(serializers.ModelSerializer):
    creator_name = serializers.SerializerMethodField()
    gmt_create = serializers.SerializerMethodField()
    last_used_at = serializers.SerializerMethodField()

    class Meta:
        model = SpaceKey
        fields = [
            "id",
            "key_prefix",
            "name",
            "enabled",
            "creator_name",
            "gmt_create",
            "last_used_at",
        ]

    def get_creator_name(self, obj):
        return obj.creator.name if obj.creator else None

    def get_gmt_create(self, obj):
        if obj.gmt_create is None:
            return None
        local = dj_timezone.localtime(obj.gmt_create)
        return local.strftime("%Y-%m-%d %H:%M:%S")

    def get_last_used_at(self, obj):
        if obj.last_used_at is None:
            return None
        local = dj_timezone.localtime(obj.last_used_at)
        return local.strftime("%Y-%m-%d %H:%M:%S")
