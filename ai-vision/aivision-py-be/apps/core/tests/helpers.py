"""Shared test helpers for core app tests."""

from django.test import SimpleTestCase, TestCase

from apps.core.models import Agent, ObservationSubjectBinding, SpaceMember, User
from apps.core.services.key_cache import key_cache


def create_local_user(identifier, **kwargs):
    """Create a valid local-account user for tests migrated from work IDs."""

    email = kwargs.pop("email", None) or f"{identifier}@example.test"
    kwargs.setdefault("name", str(identifier))
    kwargs.setdefault("status", 1)
    kwargs.setdefault("password", "!")
    return User.objects.create(
        email=email,
        normalized_email=User.normalize_email(email),
        **kwargs,
    )


def create_test_agent(**kwargs):
    """Create an Agent with the explicit subject binding required at cutover."""

    if "observation_subject_binding" not in kwargs:
        space = kwargs["space"]
        creator = space.creator
        binding, _ = ObservationSubjectBinding.objects.get_or_create(
            space=space,
            source_system="collector",
            subject_id=creator.uuid,
            defaults={
                "user": creator,
                "binding_source": "test_fixture",
                "created_by": creator,
            },
        )
        kwargs["observation_subject_binding"] = binding
    return Agent.objects.create(**kwargs)


def create_test_space_member(**kwargs):
    """Create membership together with its default collector subject binding."""

    member = SpaceMember.objects.create(**kwargs)
    ObservationSubjectBinding.objects.get_or_create(
        space=member.space,
        source_system="collector",
        subject_id=member.user.uuid,
        defaults={
            "user": member.user,
            "binding_source": "test_fixture",
            "created_by": member.user,
        },
    )
    return member


class SpaceKeySimpleTestCase(SimpleTestCase):
    """SimpleTestCase variant that isolates the shared external-auth key cache."""

    def setUp(self):
        super().setUp()
        self.reset_key_cache()

    def tearDown(self):
        self.reset_key_cache()
        super().tearDown()

    def reset_key_cache(self):
        key_cache.invalidate()


class SpaceKeyTestCase(TestCase):
    """TestCase variant that isolates the shared external-auth key cache."""

    def setUp(self):
        super().setUp()
        self.reset_key_cache()

    def tearDown(self):
        self.reset_key_cache()
        super().tearDown()

    def reset_key_cache(self):
        key_cache.invalidate()
