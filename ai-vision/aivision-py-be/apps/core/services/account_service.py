from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import User

from .local_session_service import create_session
from .personal_space_service import provision_personal_space


class AccountServiceError(Exception):
    def __init__(self, error_key, *, status=400, fields=None):
        self.error_key = error_key
        self.status = status
        self.fields = fields or []
        super().__init__(error_key)


def validate_local_password(password, *, name, email):
    candidate = User(
        name=name, email=email, normalized_email=User.normalize_email(email)
    )
    try:
        validate_password(password, user=candidate)
    except ValidationError as exc:
        raise AccountServiceError(
            "VALIDATION_ERROR", status=422, fields=["password"]
        ) from exc


def create_local_account(
    *, name, email, password, source_ip=None, user_agent=None, accept_language=""
):
    normalized_email = User.normalize_email(email)
    if not normalized_email:
        raise AccountServiceError("VALIDATION_ERROR", status=422, fields=["email"])
    validate_local_password(password, name=name, email=normalized_email)
    try:
        with transaction.atomic():
            user = User(
                name=name.strip(),
                email=normalized_email,
                normalized_email=normalized_email,
                status=1,
                product_guide_pending=True,
                password_changed_at=timezone.now(),
            )
            user.set_password(password)
            user.save()
            personal_space = provision_personal_space(
                user, accept_language=accept_language
            )
            tokens = create_session(user, source_ip=source_ip, user_agent=user_agent)
            return user, personal_space, tokens
    except IntegrityError as exc:
        raise AccountServiceError("EMAIL_ALREADY_EXISTS", status=409) from exc


def activate_pending_account(
    *, user, name, password, source_ip=None, user_agent=None, accept_language=""
):
    if user.status != User.STATUS_PENDING or user.gmt_deleted is not None:
        raise AccountServiceError("INVITATION_INVALID", status=400)
    normalized_name = str(name or "").strip()
    if not normalized_name or not password:
        raise AccountServiceError("VALIDATION_ERROR", status=422)
    validate_local_password(
        password,
        name=normalized_name,
        email=user.normalized_email,
    )
    user.name = normalized_name
    user.email = user.normalized_email
    user.status = User.STATUS_NORMAL
    user.product_guide_pending = True
    user.password_changed_at = timezone.now()
    user.set_password(password)
    user.save(
        update_fields=[
            "name",
            "email",
            "status",
            "product_guide_pending",
            "password",
            "password_changed_at",
            "gmt_modified",
        ]
    )
    personal_space = provision_personal_space(user, accept_language=accept_language)
    tokens = create_session(user, source_ip=source_ip, user_agent=user_agent)
    return personal_space, tokens


def authenticate_local_account(*, email, password, source_ip=None, user_agent=None):
    normalized_email = User.normalize_email(email)
    user = User.objects.filter(
        normalized_email=normalized_email,
        status=1,
        gmt_deleted__isnull=True,
    ).first()
    if (
        user is None
        or not user.has_usable_password()
        or not user.check_password(password)
    ):
        raise AccountServiceError("INVALID_CREDENTIALS", status=401)
    tokens = create_session(user, source_ip=source_ip, user_agent=user_agent)
    user.last_login_at = timezone.now()
    user.last_login_ip = source_ip
    user.save(update_fields=["last_login_at", "last_login_ip", "gmt_modified"])
    return user, tokens
