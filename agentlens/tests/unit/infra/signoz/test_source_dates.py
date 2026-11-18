"""Database-returned dates must not block formal or editor diagnoses."""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentlens.diagnoser.base import execute_with_applicability
from agentlens.diagnoser.executors.llm.custom.executor import LlmCustomExecutor
from agentlens.diagnoser.executors.rule.custom.executor import RuleCustomExecutor
from agentlens.diagnoser.executors.rule.custom.trial import project_trial_input
from agentlens.domain.enums import ExecutionStatus, Verdict
from agentlens.infra.signoz.ob_client import SignozObClient
from agentlens.service.datasource.signoz import TraceStore
from agentlens.service.datasource.trace import Trace

ANSWER_EXPRESSION = (
    "spans[attributes.gen_ai.span.kind:CHAT][0]"
    ".attributes.gen_ai.output.messages[role:assistant][0].content"
)
RULE_CODE = """
def evaluate(trace):
    answer = json.loads(trace["spans"][0]["attributes.gen_ai.output.messages"])[0]["content"]
    return {"metrics": [{
        "name": "quality",
        "verdict": "bad" if len(answer.strip()) <= 10 else "good",
        "reason": "answer length checked",
        "evidence": {"length": len(answer.strip())}
    }]}
"""


@pytest.mark.parametrize("trial", [False, True], ids=["formal", "trial"])
@pytest.mark.parametrize(
    ("answer", "verdict"), [("1234567890", Verdict.BAD), ("12345678901", Verdict.GOOD)]
)
def test_source_date_does_not_block_llm_or_rule_diagnosis(trial, answer, verdict):
    updated_at = datetime(2026, 10, 28, 15, 29, 16, 123456)
    physical_row = {
        "trace_id": "trace-1",
        "span_id": "span-1",
        "parent_span_id": "",
        "span_name": "Turn 1",
        "updated_at": updated_at,
        "attributes": json.dumps(
            {
                "gen_ai.span.kind": "CHAT",
                "gen_ai.output.messages": [{"role": "assistant", "content": answer}],
            }
        ),
    }
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.all.return_value = [physical_row]
    store = TraceStore(SignozObClient("unused", engine=engine))
    rows = store.read_explicit_trace_rows(
        {
            "query": {"table": "signoz_traces"},
            "space_column": "space_id",
            "space_filter": "space-1",
            "agent_product_column": "agent_product",
            "agent_product_filter": "Codex",
        },
        ["trace-1"],
    )
    if trial:
        # Editor samples cross a JSON boundary before entering the same executors.
        sample = json.loads(json.dumps({"runtime_type": "other", "spans": rows}))
        trace = project_trial_input(sample)
    else:
        trace = Trace(trace_id="trace-1", runtime_type="other", spans=rows)

    judge = MagicMock()
    judge.judge.return_value = {
        "metrics": [
            {
                "name": "quality",
                "verdict": verdict.value,
                "reason": "Synthetic Judge response",
                "evidence": {},
            }
        ]
    }
    llm = LlmCustomExecutor(
        {
            "prompt": "Answer: {{" + ANSWER_EXPRESSION + "}}",
            "required_vars": [ANSWER_EXPRESSION],
            "metrics": [{"name": "quality"}],
        },
        judge=judge,
    )
    rule = RuleCustomExecutor({"code": RULE_CODE, "metrics": [{"name": "quality"}]})
    for executor in (llm, rule):
        result = execute_with_applicability(executor, trace)
        assert result.execution.status is ExecutionStatus.SUCCEEDED
        assert result.verdict is verdict

    judge.judge.assert_called_once()
    assert judge.judge.call_args.args[0].splitlines()[0] == "Answer: " + answer
    assert rows[0]["updated_at"] == "2026-10-28T15:29:16.123456"
    assert physical_row["updated_at"] is updated_at
