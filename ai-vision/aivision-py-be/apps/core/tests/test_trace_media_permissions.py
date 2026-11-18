"""Media bytes follow the reader's role, including restored raw attributes."""
import base64
from copy import deepcopy
import json
from unittest.mock import Mock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import AgentTypeConfig, Space
from apps.core.tests.helpers import (
    create_local_user, create_test_agent, create_test_space_member,
)
from apps.core.trace_query.service import TraceQueryService
from apps.core.views.share import _ShareUser


class TraceMediaPermissionTests(TestCase):
    def setUp(self):
        self.owner = create_local_user("media-access-owner")
        self.viewer = create_local_user("media-access-viewer")
        self.developer = create_local_user("media-access-developer")
        self.space = Space.objects.create(
            name="Media", type=2, scope=1, creator=self.owner, owner=self.owner,
        )
        for user, role in ((self.owner, 1), (self.developer, 2), (self.viewer, 3)):
            create_test_space_member(space=self.space, user=user, role=role)
        kind = AgentTypeConfig.objects.create(
            agent_name="media-access", agent_product="media-access", status=1,
        )
        self.agent = create_test_agent(
            space=self.space, agent_type=kind, agent_product=kind.agent_product,
            name="Media", status=1,
        )
        self.params = {"agent_product": kind.agent_product, "agent_uuid": self.agent.uuid}
        self.secret = base64.b64encode(b"private media content").decode()
        self.media = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + self.secret}},
            {"type": "input_audio", "input_audio": {"format": "wav", "data": self.secret}},
            {"type": "video", "source": {"type": "base64", "media_type": "video/webm", "data": self.secret}},
            {"type": "file", "file": {"file_data": "data:application/pdf;base64," + self.secret}},
        ]
        self.source = {
            "traceId": "media-trace",
            "spans": [{
                "span_id": "media-span", "name": "Model request",
                "attributes": {
                    "gen_ai.input.messages": json.dumps(self.messages("user")),
                    "gen_ai.output.messages": json.dumps(self.messages("assistant")),
                },
            }],
            "semantics": {"input": self.messages("user"), "output": self.messages("assistant")},
            "metadata": {"tokens": 17, "ordinary": {"type": ["integer", "null"]}},
        }
        self.provider = Mock()
        self.provider.get_trace_detail.side_effect = lambda query: deepcopy(self.source)
        self.service = TraceQueryService()
        self.service.register_provider("signoz", self.provider, source_systems=("signoz",))

    def messages(self, role):
        return [{"role": role, "content": [{"type": "text", "text": "Keep this text"}, *deepcopy(self.media)]}]

    def read(self, user, **extra):
        return self.service.get_trace_detail(
            space=self.space, user=user, trace_id="media-trace",
            query_params={**self.params, **extra}, source_system_override="signoz",
        )

    def test_viewer_raw_and_compact_details_hide_all_input_and_output_media(self):
        for media_view in (None, "0", "1"):
            with self.subTest(media_view=media_view):
                result = self.read(self.viewer, **({"media_view": media_view} if media_view else {}))
                self.assertNotIn(self.secret, json.dumps(result))
                self.assertEqual(result["metadata"], self.source["metadata"])
                for direction in ("input", "output"):
                    content = result["semantics"][direction][0]["content"]
                    self.assertEqual(content[0], {"type": "text", "text": "Keep this text"})
                    self.assertEqual(content[1:], [{"type": "aivision_media_error", "reason": "filtered"}] * 4)
        # The storage subject belongs to the Agent owner, not the Viewer.
        self.assertEqual(self.provider.get_trace_detail.call_args.args[0].user.pk, self.owner.pk)

    def test_developer_and_owner_keep_original_input_and_output_bytes(self):
        for reader in (self.developer, self.owner):
            with self.subTest(reader=reader.uuid):
                self.assertEqual(self.read(reader), self.source)

    def test_signed_viewer_share_cannot_restore_media_as_the_agent_owner(self):
        user = _ShareUser({"uid": self.viewer.uuid, "u": self.viewer.name, "sub": self.owner.uuid})
        self.assertNotIn(self.secret, json.dumps(self.read(user)))
        developer = _ShareUser({"uid": self.developer.uuid, "u": "Developer", "sub": self.owner.uuid})
        self.assertEqual(self.read(developer), self.source)

    def test_viewer_route_and_export_response_uses_same_filter(self):
        client = APIClient()
        client.force_authenticate(self.viewer)
        url = f"/api/v1/spaces/{self.space.uuid}/signoz/trace/media-trace/detail"
        with patch("apps.core.views.trace_unified.get_default_trace_query_service", return_value=self.service):
            response = client.get(url, self.params)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.secret.encode(), response.content)
        self.assertIn(b"Keep this text", response.content)
        query = self.provider.get_trace_detail.call_args.args[0]
        self.assertEqual(query.agent_uuid, self.agent.uuid)
        self.assertEqual(query.agent_product, self.agent.agent_product)

    def test_media_aliases_in_nested_json_and_markdown_are_filtered(self):
        self.source["metadata"]["raw"] = json.dumps({
            "mime_type": "audio/ogg", "data": self.secret,
        })
        self.source["metadata"]["markdown"] = "before ![private](data:image/png;base64," + self.secret + ") after"
        self.source["metadata"]["encoded"] = json.dumps(json.dumps(self.media[0]))
        self.source["metadata"]["attachment"] = {"type": "aivision_attachment", "id": "a" * 64, "mimeType": "video/webm"}
        result = self.read(self.viewer)
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertEqual(result["metadata"]["markdown"], "before [REDACTED] after")
        self.assertEqual(result["metadata"]["attachment"], {"type": "aivision_media_error", "reason": "filtered"})

    def test_markdown_media_links_are_filtered_without_removing_ordinary_links(self):
        source = "https://media.invalid/image.png?signature=private-image"
        self.source["metadata"]["markdown"] = f"before ![caption]({source}) after"
        self.source["metadata"]["ordinary_link"] = "Read [docs](https://docs.invalid/guide)"
        self.source["metadata"]["reference"] = f"![caption][private]\n\n[private]: {source}"
        result = self.read(self.viewer)
        self.assertEqual(result["metadata"]["markdown"], "before [REDACTED] after")
        self.assertNotIn(source, json.dumps(result))
        self.assertEqual(result["metadata"]["ordinary_link"], self.source["metadata"]["ordinary_link"])

    def test_file_and_document_metadata_without_media_payload_are_preserved(self):
        self.source["metadata"]["file"] = {"type": "file", "name": "tool-result", "size": 0, "source": "tool-runner"}
        self.source["metadata"]["document"] = {"type": "document", "title": "Tool schema"}
        self.assertEqual(self.read(self.viewer)["metadata"], self.source["metadata"])

    def test_supported_string_media_url_aliases_are_filtered(self):
        url = "https://media.invalid/private?signature=secret"
        for kind, field in (("image_url", "image_url"), ("input_image", "image_url"),
                            ("audio_url", "audio_url"), ("video_url", "video_url")):
            with self.subTest(kind=kind):
                self.source["metadata"]["payload"] = {"type": kind, field: url}
                self.assertEqual(self.read(self.viewer)["metadata"]["payload"],
                                 {"type": "aivision_media_error", "reason": "filtered"})

    def test_alternate_query_entry_points_apply_the_same_reader_permission(self):
        arguments = {"space": self.space, "user": self.viewer, "query_params": self.params,
                     "source_system_override": "signoz"}
        cases = (
            ("get_trace_io_batch", {"trace_ids": ["media-trace"]}),
            ("get_span_batch", {"trace_id": "media-trace", "span_ids": ["media-span"]}),
            ("get_observation_detail", {"observation_id": "media-span", "trace_id": "media-trace"}),
            ("get_session_detail", {"session_id": "media-session"}),
            ("get_session_io", {"session_id": "media-session"}),
            ("get_session_io_batch", {"session_ids": ["media-session"]}),
        )
        for method, extra in cases:
            getattr(self.provider, method).side_effect = lambda query: deepcopy(self.source)
            with self.subTest(method=method):
                result = getattr(self.service, method)(**arguments, **extra)
                self.assertNotIn(self.secret, json.dumps(result))
                self.assertIn("Keep this text", json.dumps(result))

    def test_membership_revocation_is_applied_to_existing_signed_share(self):
        from apps.core.models import SpaceMember

        sharer = _ShareUser({"uid": self.developer.uuid, "u": "Developer", "sub": self.owner.uuid})
        self.assertEqual(self.read(sharer), self.source)
        SpaceMember.objects.filter(space=self.space, user=self.developer).update(role=3)
        self.assertNotIn(self.secret, json.dumps(self.read(sharer)))

    def test_large_media_is_removed_without_truncating_surrounding_text(self):
        self.secret = "A" * (4 * ((16 * 1024 * 1024 + 2) // 3))
        self.source = {
            "input": [{"type": "text", "text": "before"},
                      {"type": "input_audio", "input_audio": {"format": "wav", "data": self.secret}},
                      {"type": "text", "text": "after"}],
            "output": json.dumps({"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf", "data": self.secret,
            }}),
        }
        result = self.read(self.viewer)
        self.assertEqual(result["input"][0], {"type": "text", "text": "before"})
        self.assertEqual(result["input"][2], {"type": "text", "text": "after"})
        self.assertLess(len(json.dumps(result)), 300)
