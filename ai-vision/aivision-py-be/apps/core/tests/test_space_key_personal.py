"""个人空间 SpaceKey 创建/鉴权 + per-space cap 单测。"""

from rest_framework.test import APIClient

from apps.core.models import Space, SpaceKey, SpaceMember, User
from apps.core.tests.helpers import (
    create_test_space_member,
    create_local_user,
    SpaceKeyTestCase,
)
from apps.core.services.space_key_policy import MAX_KEYS_PER_SPACE


class PersonalSpaceKeyTest(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = create_local_user("100001", name="示例用户")
        # 个人空间
        self.personal = Space.objects.create(
            name="示例用户的空间", type=1, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.personal, user=self.user, role=1)
        # 业务空间
        self.business = Space.objects.create(
            name="业务空间", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.business, user=self.user, role=1)
        self.client.force_authenticate(user=self.user)

    def _create(self, space):
        url = f"/api/v1/spaces/{space.uuid}/space-keys"
        return self.client.post(url, {"name": "test"}, format="json")

    def test_personal_space_can_create_space_key(self):
        """需求1: 个人空间(type=1)支持创建 SpaceKey，不再返回 3030。"""
        resp = self._create(self.personal)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["code"] == 0
        # 完整 key = sk-aiv- + 32 位 uuid hex（共 39 字符），sk-aiv- 是 key 真实组成部分
        raw_key = body["data"]["key"]
        assert raw_key.startswith("sk-aiv-")
        assert len(raw_key) == len("sk-aiv-") + 32
        # key_prefix 是完整 key 的前 8 位片段 + 前缀
        assert body["data"]["key_prefix"].startswith("sk-aiv-")
        assert raw_key.startswith(body["data"]["key_prefix"])
        # DB 落库
        assert SpaceKey.objects.filter(space=self.personal).count() == 1

    def test_personal_space_key_authenticates(self):
        """Bearer SpaceKey 对个人空间 key 放行（不抛 business-only）。"""
        resp = self._create(self.personal)
        raw_key = resp.json()["data"]["key"]

        # openapi trace list 端点使用 Bearer SpaceKey（与 force_authenticate 用户无关）
        url = f"/openapi/v1/spaces/{self.personal.uuid}/agents/claude-code/traces/list"

        # 用不带任何身份的独立 client 验证：仅靠 Authorization Bearer 鉴权
        anon = APIClient()
        no_key = anon.post(url, {}, format="json")
        assert no_key.status_code == 401

        authed = anon.post(
            url,
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {raw_key}",
        )
        # 鉴权应通过：不应是 401（无 key）/ 403（business-only 拒绝）；
        # 后续业务可能因数据源未配置返回 500 等非鉴权错误，均视为已放行
        assert authed.status_code != 401, authed.content
        assert authed.status_code != 403, authed.content

    def test_per_space_cap_blocks_eleventh_key(self):
        """per-space cap=MAX_KEYS_PER_SPACE，第 11 个返回 3033。"""
        for _ in range(MAX_KEYS_PER_SPACE):
            resp = self._create(self.business)
            assert resp.json()["code"] == 0
        # 第 11 个
        resp = self._create(self.business)
        body = resp.json()
        assert body["code"] == 3033
        assert body["message"] == "API Key 数量已达上限"

    def test_cap_is_per_space(self):
        """cap 是 per-space：业务空间满额不阻塞个人空间。"""
        for _ in range(MAX_KEYS_PER_SPACE):
            self._create(self.business)
        resp = self._create(self.personal)
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
