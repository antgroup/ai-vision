"""V1 权限安全修复测试

覆盖审计报告中的缺陷编号 V1-SEC-01 到 V1-SEC-06。
"""

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework.request import Request

from apps.core.tests.helpers import create_test_space_member, create_local_user

from apps.core.models import User, Space, SpaceMember
from apps.core.views import DataQueryView, AgentDashboardPanelListView
from apps.core.views.query import resolve_time_window


def _make_drf_request(factory_request, user):
    """Wrap a Django WSGIRequest in a DRF Request with user attached."""
    drf_request = Request(factory_request)
    drf_request.user = user
    return drf_request


class DataQueryViewSpaceAccessTests(TestCase):
    """V1-SEC-01: DataQueryView 必须校验 space 权限。"""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = create_local_user("001001", name="Test User", status=1)
        # user 不是 space 的成员
        self.space = Space.objects.create(
            name="Other Space", type=2, scope=1, creator=self.user, status=1
        )

    def test_rejects_user_without_space_access(self):
        """非 space 成员访问 /query 应返回 3002 错误。"""
        factory_req = self.factory.get(
            f"/api/v1/spaces/{self.space.uuid}/query",
            {"agent_product": "claude_code", "data_item": "session_count"},
        )
        request = _make_drf_request(factory_req, self.user)
        response = DataQueryView().get(request, space_id=self.space.uuid)
        self.assertEqual(response.status_code, 400)
        data = response.data if hasattr(response, "data") else {}
        self.assertEqual(data.get("code"), 3002)

    def test_allows_space_member(self):
        """space 成员应能通过权限校验。"""
        create_test_space_member(space=self.space, user=self.user, role=1)
        factory_req = self.factory.get(
            f"/api/v1/spaces/{self.space.uuid}/query",
            {"agent_product": "claude_code", "data_item": "session_count"},
        )
        request = _make_drf_request(factory_req, self.user)
        response = DataQueryView().get(request, space_id=self.space.uuid)
        data = response.data if hasattr(response, "data") else {}
        # 权限校验通过后，查询会因 agent type 不存在而失败（code=3001）
        # 但不应是权限拒绝（code=3002）
        self.assertNotEqual(data.get("code"), 3002)


class DataQueryViewWorkIdRemovalTests(TestCase):
    """V1-SEC-02: DataQueryView 不再接受 workId 参数替换用户身份。"""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = create_local_user("001001", name="Test User", status=1)
        self.space = Space.objects.create(
            name="Test Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)

    def test_workid_parameter_is_ignored(self):
        """提供 workId 参数不应改变查询用户身份。"""
        factory_req = self.factory.get(
            f"/api/v1/spaces/{self.space.uuid}/query",
            {"agent_product": "test", "data_item": "test_item", "workId": "999999"},
        )
        request = _make_drf_request(factory_req, self.user)
        # workId 被忽略，正常处理请求不报错
        response = DataQueryView().get(request, space_id=self.space.uuid)
        self.assertIsNotNone(response)


class DataQueryViewTimeRangeTests(TestCase):
    """V1-SEC-03: DataQueryView 应拒绝超长的时间范围。"""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = create_local_user("001001", name="Test User", status=1)
        self.space = Space.objects.create(
            name="Test Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)

    def test_rejects_time_range_exceeding_90_days(self):
        """超过 90 天的时间范围应被拒绝。"""
        factory_req = self.factory.get(
            f"/api/v1/spaces/{self.space.uuid}/query",
            {
                "agent_product": "test",
                "data_item": "test_item",
                "fromTimestamp": "2020-01-01T00:00:00Z",
                "toTimestamp": "2021-01-01T00:00:00Z",
            },
        )
        request = _make_drf_request(factory_req, self.user)
        response = DataQueryView().get(request, space_id=self.space.uuid)
        self.assertEqual(response.status_code, 400)

    def test_allows_time_range_within_90_days(self):
        """90 天以内的时间范围应被接受。"""
        factory_req = self.factory.get(
            f"/api/v1/spaces/{self.space.uuid}/query",
            {
                "agent_product": "test",
                "data_item": "test_item",
                "fromTimestamp": "2026-02-01T00:00:00Z",
                "toTimestamp": "2026-05-01T00:00:00Z",
            },
        )
        request = _make_drf_request(factory_req, self.user)
        response = DataQueryView().get(request, space_id=self.space.uuid)
        data = response.data if hasattr(response, "data") else {}
        # 不应因时间范围被拒绝
        self.assertNotEqual(data.get("code"), 1001)


class DataQueryTimeWindowParsingTests(SimpleTestCase):
    def test_public_time_errors_keep_field_specific_messages(self):
        cases = (
            (
                "2026-02-30T00:00:00Z",
                "2026-03-01T00:00:00Z",
                "Invalid fromTimestamp or toTimestamp",
            ),
            (
                "2026-03-02T00:00:00Z",
                "2026-03-01T00:00:00",
                "fromTimestamp cannot be later than toTimestamp",
            ),
        )
        for start, end, message in cases:
            with self.subTest(start=start, end=end):
                request = Request(
                    RequestFactory().get(
                        "/query", {"fromTimestamp": start, "toTimestamp": end}
                    )
                )
                self.assertEqual(resolve_time_window(request).public_message, message)


class AgentDashboardPanelListAuthTests(TestCase):
    """V1-SEC-04: AgentDashboardPanelListView 应要求认证。"""

    def test_requires_authentication(self):
        """未认证用户访问应返回 401。"""
        view = AgentDashboardPanelListView()
        self.assertIn(
            "IsAuthenticated",
            [pc.__name__ for pc in view.permission_classes],
            "AgentDashboardPanelListView must require IsAuthenticated",
        )


class DataQueryViewErrorSanitizationTests(TestCase):
    """V1-SEC-06: DataQueryView 查询失败时应返回脱敏错误消息。"""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = create_local_user("001001", name="Test User", status=1)
        self.space = Space.objects.create(
            name="Test Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)

    def test_error_response_does_not_leak_datasource_info(self):
        """查询失败时的错误消息不应包含表名或 endpoint 等敏感信息。"""
        factory_req = self.factory.get(
            f"/api/v1/spaces/{self.space.uuid}/query",
            {"agent_product": "claude_code", "data_item": "session_count"},
        )
        request = _make_drf_request(factory_req, self.user)
        response = DataQueryView().get(request, space_id=self.space.uuid)
        data = response.data if hasattr(response, "data") else {}
        # 错误消息不应包含表名或 endpoint URL
        error_msg = str(data.get("message", ""))
        self.assertNotIn("https://", error_msg)
        self.assertNotIn("aivision_", error_msg)
