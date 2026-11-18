from django.db import transaction
from django.utils.translation.trans_real import parse_accept_lang_header

from apps.core.models import ObservationSubjectBinding, Space, SpaceMember, User

_PERSONAL_SPACE_SUFFIXES = {
    "en": " Personal Space",
    "ja": "の個人スペース",
    "zh": "的个人空间",
}


class PersonalSpaceInvariantError(Exception):
    pass


def _personal_space_name(name, accept_language):
    suffix = _PERSONAL_SPACE_SUFFIXES["en"]
    for language, quality in parse_accept_lang_header(accept_language):
        base = language.partition("-")[0]
        if quality > 0 and base in _PERSONAL_SPACE_SUFFIXES:
            suffix = _PERSONAL_SPACE_SUFFIXES[base]
            if base == "zh" and name[-1:].isascii():
                suffix = " " + suffix
            break
    max_name_length = Space._meta.get_field("name").max_length - len(suffix)
    return name[:max_name_length].rstrip() + suffix


def provision_personal_space(user, *, accept_language=""):
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        spaces = list(
            Space.objects.select_for_update().filter(
                creator=locked_user,
                type=1,
                status=1,
                gmt_deleted__isnull=True,
            )
        )
        if len(spaces) > 1:
            raise PersonalSpaceInvariantError(
                "User has multiple active personal spaces"
            )
        if spaces:
            space = spaces[0]
        else:
            space = Space.objects.create(
                name=_personal_space_name(locked_user.name, accept_language),
                type=1,
                scope=1,
                creator=locked_user,
                status=1,
            )
        SpaceMember.objects.update_or_create(
            space=space,
            user=locked_user,
            defaults={"role": 1, "gmt_deleted": None},
        )
        ObservationSubjectBinding.objects.update_or_create(
            space=space,
            source_system="collector",
            subject_id=locked_user.uuid,
            defaults={
                "user": locked_user,
                "binding_source": "account_default",
                "status": 1,
                "created_by": locked_user,
                "gmt_deleted": None,
            },
        )
        return space
