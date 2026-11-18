"""A slow source/provider must never retain configuration locks."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from agentlens.diagnoser.definitions.specialists import get_specialist_definition, specialist_spec
from agentlens.infra.llm_judge import LLMJudge
from agentlens.infra.ob.models import DiagnoserModel, ModelConnectionModel
from agentlens.service.specialists import SpecialistError, trial_specialist
from agentlens.web.schemas.specialist import SpecialistTrialInput
from tests.unit.diagnoser.test_specialist_executors import llm_result
from tests.unit.diagnoser.test_specialist_materials import trace
from tests.unit.diagnoser.test_specialist_policies import relevance
from tests.unit.web.routes.test_specialist_routes import OfflineSession, source_spec


class TransactionSession(OfflineSession):
    active = False

    def in_transaction(self):
        return self.active

    @contextmanager
    def begin(self):
        self.active = True
        try:
            yield
            self.commit()
        except BaseException:
            self.rollback()
            raise
        finally:
            self.active = False


def seed(session):
    session.rows = [
        DiagnoserModel(
            uuid="saved",
            space_uuid="space",
            agent_uuid="agent",
            name="synthetic",
            type="builtin",
            mode="llm",
            version="1.0.0",
            spec=specialist_spec(
                get_specialist_definition("answer_relevance", "explicit_current_text"), relevance()
            ),
        )
    ]
    session.models = [
        ModelConnectionModel(
            uuid="model",
            space_uuid="space",
            status="available",
            endpoint="https://example.invalid/v1",
            model_name="synthetic",
            api_key="synthetic",
        )
    ]
    return SpecialistTrialInput(
        space_uuid="space",
        agent_uuid="agent",
        dataset_spec=source_spec(),
        trace_id="t",
        diagnoser_uuid="saved",
    )


def test_source_and_provider_run_after_transaction(monkeypatch):
    session = TransactionSession()
    payload = seed(session)
    expected = deepcopy(session.rows[0].spec)
    events = []

    def read(*args, **kwargs):
        assert not session.active and session.commits == 1
        events.append("source")
        session.rows[0].spec = {}
        session.models[0].endpoint = "https://replacement.invalid"
        session.models[0].api_key = "replacement"
        return trace().spans

    def respond(request):
        assert not session.active and session.commits == 1
        assert str(request.url) == "https://example.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic"
        events.append("http")
        import json

        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(llm_result())}}]}
        )

    real = LLMJudge
    monkeypatch.setattr(
        "agentlens.diagnoser.executors.llm.base.LLMJudge",
        lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
    )
    result = trial_specialist(session, SimpleNamespace(read_explicit_trace_rows=read), payload)
    assert result["verdict"] == "good" and events == ["source", "http"]
    assert result["specialist"]["configuration_digest"] == expected["configuration_digest"]


def test_caller_transaction_is_never_committed_or_rolled_back():
    session = TransactionSession()
    payload = seed(session)
    session.active = True
    with pytest.raises(SpecialistError, match="SPECIALIST_TRANSACTION_ACTIVE"):
        trial_specialist(session, None, payload)
    assert session.commits == session.rollbacks == 0 and session.active


def test_configuration_failure_rolls_back_owned_transaction():
    session = TransactionSession()
    payload = seed(session)
    session.rows = []
    with pytest.raises(SpecialistError, match="SPECIALIST_AGENT_UNAVAILABLE"):
        trial_specialist(session, None, payload)
    assert session.rollbacks == 1 and not session.active


@pytest.mark.parametrize("stage", ["source", "http"])
def test_owned_database_edit_completes_while_trial_io_waits(session, engine, monkeypatch, stage):
    import json
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import select, text, update
    from sqlalchemy.orm import Session

    assert session.scalar(text("SELECT @@autocommit")) == 0
    session.commit()
    fixture = TransactionSession()
    payload = seed(fixture)
    fixture.models[0].name = "trial-model"
    session.add_all(fixture.rows + fixture.models)
    session.commit()
    entered, release = Event(), Event()
    captured = []

    def wait():
        entered.set()
        assert release.wait(8), "test did not release slow IO"

    def read(*args, **kwargs):
        if stage == "source":
            wait()
        return trace().spans

    def respond(request):
        if stage == "http":
            wait()
        captured.append((str(request.url), request.headers["authorization"]))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(llm_result())}}]}
        )

    monkeypatch.setattr(
        "agentlens.diagnoser.executors.llm.base.LLMJudge",
        lambda *a, **kw: LLMJudge(*a, **kw, transport=httpx.MockTransport(respond)),
    )

    def run():
        with Session(engine) as owned:
            return trial_specialist(owned, SimpleNamespace(read_explicit_trace_rows=read), payload)

    def edit():
        with Session(engine) as writer, writer.begin():
            writer.execute(
                update(DiagnoserModel)
                .where(DiagnoserModel.uuid == "saved")
                .values(name="edited-during-trial")
            )
            writer.execute(
                update(ModelConnectionModel)
                .where(ModelConnectionModel.uuid == "model")
                .values(api_key="new-key", endpoint="https://new.invalid/v1")
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(run)
        try:
            assert entered.wait(5)
            pool.submit(edit).result(timeout=3)
        finally:
            release.set()
        assert running.result(timeout=5)["verdict"] == "good"
    assert captured == [("https://example.invalid/v1/chat/completions", "Bearer synthetic")]
    session.expire_all()
    assert (
        session.scalar(select(DiagnoserModel.name).where(DiagnoserModel.uuid == "saved"))
        == "edited-during-trial"
    )


def test_owned_database_external_transaction_is_preserved(session, engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    fixture = TransactionSession()
    payload = seed(fixture)
    with engine.connect() as connection, connection.begin():
        with Session(bind=connection) as borrowed:
            assert not borrowed.in_transaction()
            with pytest.raises(SpecialistError, match="SPECIALIST_TRANSACTION_ACTIVE"):
                trial_specialist(borrowed, None, payload)
            assert connection.in_transaction()
            assert connection.scalar(text("SELECT 1")) == 1
