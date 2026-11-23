from datetime import datetime
from typing import NamedTuple
from urllib.parse import urlencode
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import McpServerConfig, SkillConfig, Space
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_space_member,
)


class ConfigApi(NamedTuple):
    feature: str
    collection: str
    name_key: str
    requested_name_key: str
    model: type[McpServerConfig] | type[SkillConfig]
    name_field: str


CONFIG_APIS = (
    ConfigApi("mcp", "configs", "serverId", "requestedServerId", McpServerConfig, "server_id"),
    ConfigApi("skill", "skills", "name", "requestedName", SkillConfig, "name"),
)


class LabsImportConflictTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_local_user("import-owner")
        self.space = Space.objects.create(
            name="Import review", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _url(self, api):
        return f"/api/v2/spaces/{self.space.uuid}/labs/{api.feature}/configs"

    def _item(self, api, name, description="Updated"):
        item = {api.name_key: name, "description": description, "raw": {}}
        if api.feature == "mcp":
            item.update(transport="stdio", command="fixture-command")
        return item

    def _preview(self, api, names):
        return self.client.post(
            f"{self._url(api)}?view=conflicts", {"names": names}, format="json"
        )

    def _write(self, api, records, overwrites):
        return self.client.put(
            f"{self._url(api)}?response=receipts",
            {api.collection: records, "overwrites": overwrites},
            format="json",
        )

    def _delete(self, api, name):
        return self.client.delete(
            f"{self._url(api)}?{urlencode({api.name_key: name})}"
        )

    def test_preview_returns_only_active_matches_in_this_space(self):
        other_space = Space.objects.create(
            name="Other", type=2, scope=1, creator=self.user, status=1
        )
        for api in CONFIG_APIS:
            with self.subTest(feature=api.feature):
                model, field = api.model, api.name_field
                live = model.objects.create(
                    space=self.space, **{field: "Docs"}, raw={"content": "private body"}
                )
                version = live.imported_at if api.feature == "mcp" else live.gmt_modified
                model.objects.create(
                    space=self.space, **{field: "retired"}, gmt_deleted=timezone.now()
                )
                model.objects.create(space=other_space, **{field: "other-only"})
                requested = (
                    "docs"
                    if model.objects.filter(space=self.space, **{field: "docs"}).exists()
                    else "Docs"
                )
                response = self._preview(
                    api, ["other-only", requested, "retired", "absent"]
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(
                    response.data["data"],
                    [{
                        "requestedName": requested,
                        "name": "Docs",
                        "version": version.isoformat(),
                    }],
                )

    def test_guarded_batch_rolls_back_on_a_new_conflict(self):
        for api in CONFIG_APIS:
            with self.subTest(feature=api.feature):
                model, field = api.model, api.name_field
                existing = model.objects.create(
                    space=self.space, **{field: "docs"}, description="Keep this"
                )
                response = self._write(
                    api,
                    [self._item(api, "other"), self._item(api, "docs")],
                    [],
                )
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data["data"]["requestedName"], "docs")
                self.assertNotIn("conflictingRequestedName", response.data["data"])
                existing.refresh_from_db()
                self.assertEqual(existing.description, "Keep this")
                self.assertFalse(
                    model.objects.filter(space=self.space, **{field: "other"}).exists()
                )

    def test_overwrite_confirmation_is_bound_to_current_name_and_version(self):
        for api in CONFIG_APIS:
            with self.subTest(feature=api.feature):
                model, field = api.model, api.name_field
                existing = model.objects.create(space=self.space, **{field: "Docs"})
                requested = (
                    "docs"
                    if model.objects.filter(space=self.space, **{field: "docs"}).exists()
                    else "Docs"
                )
                preview = self._preview(api, [requested])
                self.assertEqual(preview.status_code, 200, preview.data)
                [match] = preview.data["data"]
                approval = [{"name": requested, "version": match["version"]}]
                with patch(
                    "django.utils.timezone.now",
                    return_value=datetime.fromisoformat(match["version"]),
                ):
                    response = self._write(api, [self._item(api, requested)], approval)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["data"][0][api.requested_name_key], requested)
                self.assertEqual(response.data["data"][0][api.name_key], "Docs")
                existing.refresh_from_db()
                self.assertEqual(existing.description, "Updated")
                self.assertEqual(model.objects.filter(space=self.space).count(), 1)
                latest = self._preview(api, [requested]).data["data"][0]
                self.assertNotEqual(latest["version"], approval[0]["version"])

                response = self._write(
                    api, [self._item(api, requested, "Stale edit")], approval
                )
                self.assertEqual(response.status_code, 409, response.data)
                existing.refresh_from_db()
                self.assertEqual(existing.description, "Updated")

    def test_one_stored_config_cannot_be_written_twice_in_a_guarded_batch(self):
        for api in CONFIG_APIS:
            with self.subTest(feature=api.feature):
                model, field = api.model, api.name_field
                record = self._item(api, "once")
                response = self._write(api, [record, record], [])
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(
                    response.data["data"],
                    {
                        "requestedName": "once",
                        "conflictingRequestedName": "once",
                        "name": "once",
                        "version": None,
                    },
                )
                self.assertFalse(
                    model.objects.filter(space=self.space, **{field: "once"}).exists()
                )

    def test_delete_query_accepts_slash_and_stale_approval_cannot_restore_row(self):
        for api in CONFIG_APIS:
            with self.subTest(feature=api.feature):
                model, field = api.model, api.name_field
                name = "docs/tools"
                existing = model.objects.create(space=self.space, **{field: name})
                preview = self._preview(api, [name])
                self.assertEqual(preview.status_code, 200, preview.data)
                [match] = preview.data["data"]
                self.assertEqual(self._delete(api, name).status_code, 200)
                existing.refresh_from_db()
                self.assertIsNotNone(existing.gmt_deleted)
                self.assertEqual(self._preview(api, [name]).data["data"], [])

                approval = [{"name": name, "version": match["version"]}]
                stale = self._write(api, [self._item(api, name)], approval)
                self.assertEqual(stale.status_code, 409, stale.data)
                existing.refresh_from_db()
                self.assertIsNotNone(existing.gmt_deleted)

                restored = self._write(api, [self._item(api, name)], [])
                self.assertEqual(restored.status_code, 200, restored.data)
                existing.refresh_from_db()
                self.assertIsNone(existing.gmt_deleted)
                stale = self._write(api, [self._item(api, name)], approval)
                self.assertEqual(stale.status_code, 409, stale.data)

    def test_conflict_reads_and_query_deletion_require_admin(self):
        reader = create_local_user("import-reader")
        create_test_space_member(space=self.space, user=reader, role=3)
        self.client.force_authenticate(user=reader)
        for api in CONFIG_APIS:
            with self.subTest(feature=api.feature):
                model, field = api.model, api.name_field
                existing = model.objects.create(space=self.space, **{field: "docs"})
                self.assertEqual(self._preview(api, ["docs"]).status_code, 403)
                self.assertEqual(self._delete(api, "docs").status_code, 403)
                existing.refresh_from_db()
                self.assertIsNone(existing.gmt_deleted)

    def test_invalid_conflict_payloads_do_not_write(self):
        for api in CONFIG_APIS:
            with self.subTest(feature=api.feature):
                model = api.model
                code = 3005 if api.feature == "mcp" else 3101
                self.assertEqual(
                    self._preview(api, ["name"] * 51).status_code, 400
                )
                preview = self._preview(api, [""])
                self.assertEqual(preview.status_code, 400)
                self.assertEqual(preview.data, {
                    "code": code,
                    "message": "names must contain 1 to 50 valid configuration names",
                    "data": None,
                })
                invalid_overwrites = (
                    (None, "overwrites must be a list of submitted names and versions"),
                    ({}, "overwrites must be a list of submitted names and versions"),
                    ([{"name": "other", "version": "revision"}], "Invalid overwrite confirmation"),
                )
                for invalid, message in invalid_overwrites:
                    response = self._write(
                        api, [self._item(api, "docs")], invalid
                    )
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assertEqual(response.data, {"code": code, "message": message, "data": None})
                self.assertFalse(model.objects.filter(space=self.space).exists())

    def test_conflict_endpoints_hide_internal_value_errors(self):
        for api in CONFIG_APIS:
            code = 3005 if api.feature == "mcp" else 3101
            module = f"apps.core.views.labs_{api.feature}"
            with self.subTest(feature=api.feature):
                with patch(
                    f"{module}.conflict_matches",
                    side_effect=ValueError("Fixture storage diagnostic"),
                ):
                    response = self._preview(api, ["docs"])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {
                    "code": code,
                    "message": "Failed to check configuration names",
                    "data": None,
                })
                with patch(
                    f"{module}.ImportWriteGuard",
                    side_effect=ValueError("Fixture validation diagnostic"),
                ):
                    response = self._write(api, [self._item(api, "docs")], [])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {
                    "code": code,
                    "message": "Invalid overwrite confirmation",
                    "data": None,
                })
                self.assertFalse(api.model.objects.filter(space=self.space).exists())
