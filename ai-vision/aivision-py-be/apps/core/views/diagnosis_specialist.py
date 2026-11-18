"""Four explicit endpoints for optional specialist checks."""

from rest_framework.views import APIView

from apps.core.services import diagnosis_specialist as service
from apps.core.services.diagnosis_specialist_input import SpecialistError
from apps.core.services.diagnosis_trial_timeout import LLM_TRIAL_TIMEOUT_MESSAGE

from .common import error_response, success_response


class SpecialistAPIView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, SpecialistError):
            return error_response(
                LLM_TRIAL_TIMEOUT_MESSAGE
                if exc.key == "SPECIALIST_UPSTREAM_TIMEOUT"
                else exc.key,
                exc.status * 10,
                status=exc.status,
                data={"error_key": exc.key},
            )
        return super().handle_exception(exc)


class SpecialistCatalogView(SpecialistAPIView):
    def get(self, request, space_id):
        return success_response(
            service.catalog(
                request.user, space_id, request.query_params.get("agent_uuid", "")
            )
        )


class SpecialistSyncView(SpecialistAPIView):
    def post(self, request, space_id):
        return success_response(
            service.sync(
                request.user,
                space_id,
                request.query_params.get("agent_uuid", ""),
                request.data,
            )
        )


class SpecialistConfigurationView(SpecialistAPIView):
    def patch(self, request, space_id, diagnoser_uuid):
        return success_response(
            service.configure(
                request.user,
                space_id,
                request.query_params.get("agent_uuid", ""),
                diagnoser_uuid,
                request.data,
            )
        )


class SpecialistTrialView(SpecialistAPIView):
    def post(self, request, space_id):
        return success_response(
            service.trial(
                request.user,
                space_id,
                request.query_params.get("agent_uuid", ""),
                request.data,
            )
        )
