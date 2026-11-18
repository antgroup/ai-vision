"""Offline HTTP-point selection, cache visibility and resource guards."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import override_settings
from rest_framework.test import APIRequestFactory

from apps.core.views import agentlens_proxy as proxy


class DiagnosisStatsPointTests(TestCase):
    def request(self, **params):
        view = proxy.DiagnosisStatsView()
        request = view.initialize_request(APIRequestFactory().get("/", params))
        request.user = SimpleNamespace(uuid="caller")
        return view, request

    @override_settings(AGENTLENS_BASE_URL="http://fixture", AGENTLENS_TOKEN="fixture")
    @patch.object(proxy, "http_requests")
    @patch.object(proxy, "cache")
    def test_explicit_point_ignores_stale_empty_cache_and_caches_only_visible_point(
        self, cache, http
    ):
        cache.get.return_value = {"categories": [], "tasks": []}
        first = Mock(ok=True)
        first.json.return_value = {"categories": [], "tasks": []}
        second = Mock(ok=True)
        second.json.return_value = {"categories": [], "tasks": [{"task_uuid": "task"}]}
        http.get.side_effect = [first, second]
        params = {"job_uuid": "job", "task_uuid": "task"}
        response = proxy._forward_get_cached(
            "/api/v1/diagnosis-stats",
            params,
            ttl=30,
            namespace="caller",
            cache_empty_tasks=False,
        )
        self.assertEqual(response.data["data"]["tasks"], [])
        cache.set.assert_not_called()
        response = proxy._forward_get_cached(
            "/api/v1/diagnosis-stats",
            params,
            ttl=30,
            namespace="caller",
            cache_empty_tasks=False,
        )
        self.assertEqual(response.data["data"]["tasks"][0]["task_uuid"], "task")
        cache.set.assert_called_once()
        # Default trend behavior still accepts its ordinary cached empty result.
        response = proxy._forward_get_cached(
            "/api/v1/diagnosis-stats", {"job_uuid": "job"}, ttl=30, namespace="caller"
        )
        self.assertEqual(response.data["data"]["tasks"], [])
        self.assertEqual(http.get.call_count, 2)

    def test_point_requires_single_task_job_agent_and_same_job_before_cache(self):
        with (
            patch.object(
                proxy,
                "_check_space",
                return_value=(SimpleNamespace(uuid="space"), None),
            ),
            patch.object(proxy, "_require_scoped_agent", return_value=("agent", None)),
            patch.object(proxy, "_get_scoped_remote_task") as task,
            patch.object(proxy, "_get_scoped_remote_resource") as job,
            patch.object(proxy, "_forward_get_cached") as cached,
        ):
            task.return_value = (
                proxy.success_response({"uuid": "task", "job_uuid": "other-job"}),
                None,
            )
            job.return_value = (
                proxy.success_response(
                    {"uuid": "job", "space_uuid": "space", "agent_uuid": "agent"}
                ),
                None,
            )
            view, request = self.request(
                job_uuid="job", task_uuid="task", agent_uuid="agent"
            )
            response = view.get(request, "space")
            self.assertEqual(response.status_code, 404)
            cached.assert_not_called()
            for params in (
                {"job_uuid": "job", "task_uuid": ""},
                {"job_uuid": ["job", "other"], "task_uuid": "task"},
                {"job_uuid": "job", "task_uuid": ["task", "other"]},
            ):
                view, request = self.request(**params)
                self.assertEqual(view.get(request, "space").status_code, 400)
            cached.assert_not_called()

    def test_point_forwards_only_selected_ids_and_server_owner_after_guards(self):
        with (
            patch.object(
                proxy,
                "_check_space",
                return_value=(SimpleNamespace(uuid="space"), None),
            ),
            patch.object(proxy, "_require_scoped_agent", return_value=("agent", None)),
            patch.object(
                proxy,
                "_get_scoped_remote_task",
                return_value=(
                    proxy.success_response({"uuid": "task", "job_uuid": "job"}),
                    None,
                ),
            ),
            patch.object(
                proxy,
                "_get_scoped_remote_resource",
                return_value=(
                    proxy.success_response(
                        {"uuid": "job", "space_uuid": "space", "agent_uuid": "agent"}
                    ),
                    None,
                ),
            ),
            patch.object(proxy, "_forward_get_cached") as cached,
        ):
            view, request = self.request(
                job_uuid="job",
                task_uuid="task",
                agent_uuid="agent",
                space_uuid="foreign",
                owner_id="foreign",
                sql="unsafe",
            )
            view.get(request, "space")
            args, kw = cached.call_args
            self.assertEqual(
                args[1],
                {"job_uuid": ["job"], "task_uuid": ["task"], "owner_id": "caller"},
            )
            self.assertFalse(kw["cache_empty_tasks"])

    def test_point_rejects_missing_or_wrong_agent_before_cache(self):
        with (
            patch.object(
                proxy,
                "_check_space",
                return_value=(SimpleNamespace(uuid="space"), None),
            ),
            patch.object(proxy, "Agent") as agents,
            patch.object(
                proxy,
                "_get_scoped_remote_task",
                return_value=(
                    proxy.success_response({"uuid": "task", "job_uuid": "job"}),
                    None,
                ),
            ),
            patch.object(
                proxy,
                "_get_scoped_remote_resource",
                return_value=(
                    proxy.success_response(
                        {"uuid": "job", "space_uuid": "space", "agent_uuid": "other"}
                    ),
                    None,
                ),
            ),
            patch.object(proxy, "_forward_get_cached") as cached,
        ):
            agents.objects.filter.return_value.exists.return_value = True
            view, request = self.request(job_uuid="job", task_uuid="task")
            self.assertEqual(view.get(request, "space").status_code, 400)
            view, request = self.request(
                job_uuid="job", task_uuid="task", agent_uuid="agent"
            )
            self.assertEqual(view.get(request, "space").status_code, 404)
            cached.assert_not_called()

    def test_point_foreign_task_job_space_is_rejected_before_cache(self):
        def forward(path):
            if path == "/api/v1/task/task":
                return proxy.success_response(
                    {"uuid": "task", "job_uuid": "foreign-job"}
                )
            return proxy.success_response(
                {"uuid": "foreign-job", "space_uuid": "foreign", "agent_uuid": "agent"}
            )

        with (
            patch.object(
                proxy,
                "_check_space",
                return_value=(SimpleNamespace(uuid="space"), None),
            ),
            patch.object(proxy, "_require_scoped_agent", return_value=("agent", None)),
            patch.object(proxy, "_forward_get", side_effect=forward),
            patch.object(proxy, "_forward_get_cached") as cached,
        ):
            view, request = self.request(
                job_uuid="job", task_uuid="task", agent_uuid="agent"
            )
            self.assertEqual(view.get(request, "space").status_code, 404)
            cached.assert_not_called()
