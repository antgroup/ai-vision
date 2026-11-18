from apps.core.models import ObservationSubjectBinding, SpaceMember


class ObservationSubjectError(Exception):
    def __init__(self, error_key="SUBJECT_BINDING_MISSING"):
        self.error_key = error_key
        super().__init__(error_key)


def resolve_subjects(*, space, user, source_system):
    if getattr(space, "pk", None) is None or getattr(user, "pk", None) is None:
        return []
    if not SpaceMember.objects.filter(
        space=space,
        user=user,
        gmt_deleted__isnull=True,
        user__status=1,
        user__gmt_deleted__isnull=True,
    ).exists():
        return []
    return list(
        ObservationSubjectBinding.objects.filter(
            space=space,
            user=user,
            source_system=source_system,
            status=1,
            gmt_deleted__isnull=True,
        )
        .order_by("id")
        .values_list("subject_id", flat=True)
    )


def require_default_subject(*, space, user, source_system="collector"):
    signed_subject = getattr(user, "observation_subject_id", None)
    if signed_subject:
        return str(signed_subject)
    subjects = resolve_subjects(
        space=space,
        user=user,
        source_system=source_system,
    )
    if not subjects:
        raise ObservationSubjectError()
    return subjects[0]


def require_agent_subject(agent):
    """Return the only subject identity an Agent is allowed to query or ingest."""

    binding = getattr(agent, "observation_subject_binding", None)
    if (
        binding is None
        or binding.space_id != agent.space_id
        or binding.status != 1
        or binding.gmt_deleted is not None
        or binding.user.status != 1
        or binding.user.gmt_deleted is not None
    ):
        raise ObservationSubjectError()
    return binding.subject_id


def subject_name_map(*, space, source_system, subject_ids):
    return {
        binding.subject_id: binding.user.name
        for binding in ObservationSubjectBinding.objects.select_related("user").filter(
            space=space,
            source_system=source_system,
            subject_id__in=subject_ids,
            status=1,
            gmt_deleted__isnull=True,
            user__status=1,
            user__gmt_deleted__isnull=True,
        )
    }
