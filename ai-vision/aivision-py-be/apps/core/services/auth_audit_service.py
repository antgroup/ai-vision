from apps.core.models import AuthAuditLog


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64]
    return str(request.META.get("REMOTE_ADDR", ""))[:64] or None


def write_auth_audit(
    request,
    *,
    event,
    success,
    actor_user=None,
    space=None,
    session_uuid=None,
    error_key=None,
):
    return AuthAuditLog.objects.create(
        event=event,
        success=success,
        actor_user=actor_user,
        space=space,
        session_uuid=session_uuid,
        request_id=(request.META.get("HTTP_X_REQUEST_ID") or "")[:128] or None,
        source_ip=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:512] or None,
        error_key=error_key,
    )
