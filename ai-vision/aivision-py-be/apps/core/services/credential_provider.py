"""
凭证提供者抽象层。

当前实现从 Langfuse 获取 PK/SK，后续去 Langfuse 化时替换 Provider 即可。
"""

import base64
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests as http_requests

logger = logging.getLogger(__name__)


@dataclass
class CredentialPair:
    public_key: str
    secret_key: str


class CredentialProvider(ABC):
    """凭证提供者抽象基类"""

    @abstractmethod
    def create_project_and_credentials(
        self, project_name: str, org_id: str | None = None
    ) -> tuple[dict, CredentialPair, CredentialPair]:
        """
        创建 project 并返回两组凭证。

        Returns:
            (project_info, read_pair, write_pair)
            project_info: dict with upstream_project_id, upstream_project_name, upstream_project_slug
        """
        ...

    @abstractmethod
    def create_api_key(self, project_id: str) -> CredentialPair:
        """在已有 project 下创建一组新的 API Key"""
        ...

    @abstractmethod
    def delete_api_key(self, api_key_id: str) -> bool:
        """删除指定的 API Key"""
        ...


class LangfuseCredentialProvider(CredentialProvider):
    """从 Langfuse 获取实际 PK/SK 的凭证提供者"""

    def __init__(self, base_url: str, admin_public_key: str, admin_secret_ciphertext: str):
        self.base_url = base_url.rstrip("/")
        self.admin_public_key = admin_public_key
        self.admin_secret_ciphertext = admin_secret_ciphertext

    def _auth_header(self) -> dict:
        credentials = base64.b64encode(
            f"{self.admin_public_key}:{self.admin_secret_ciphertext}".encode()
        ).decode()
        return {"Authorization": f"Basic {credentials}"}

    def create_project_and_credentials(
        self, project_name: str, org_id: str | None = None
    ) -> tuple[dict, CredentialPair, CredentialPair]:
        # 1. 创建 project
        payload = {"name": project_name}
        if org_id:
            payload["orgId"] = org_id

        headers = {**self._auth_header(), "Content-Type": "application/json"}
        resp = http_requests.post(
            f"{self.base_url}/api/public/projects",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        project_data = resp.json()
        project_id = project_data.get("id", "")
        project_info = {
            "upstream_project_id": project_id,
            "upstream_project_name": project_data.get("name", project_name),
            "upstream_project_slug": project_data.get("slug", ""),
        }

        # 2. 创建两组 API Key（READ + WRITE）
        read_pair = self.create_api_key(project_id)
        write_pair = self.create_api_key(project_id)

        return project_info, read_pair, write_pair

    def create_api_key(self, project_id: str) -> CredentialPair:
        headers = {**self._auth_header(), "Content-Type": "application/json"}
        resp = http_requests.post(
            f"{self.base_url}/api/public/projects/{project_id}/apiKeys",
            json={"note": "Created by AIVision"},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return CredentialPair(
            public_key=data["publicKey"],
            secret_key=data["secretKey"],
        )

    def delete_api_key(self, api_key_id: str) -> bool:
        try:
            resp = http_requests.delete(
                f"{self.base_url}/api/public/api-keys/{api_key_id}",
                headers=self._auth_header(),
                timeout=30,
            )
            resp.raise_for_status()
            return True
        except http_requests.RequestException:
            logger.warning("Failed to delete Langfuse API key: %s", api_key_id)
            return False
