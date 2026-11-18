"""Three real Hermes CLI processes, native resume, and output-cap continuation.

This probe checks observed user-turn numbering against independent raw hooks
and OTLP, never gives the production plugin an ordinal or changes host state.
Real host compaction is exercised separately by hermes_compression.py.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path

from drivers.hermes import run_case
from models import CaseContext


def assess(report):
    from fake_provider import HERMES_LENGTH_CONTINUATION_PROMPT
    result = report["result"]
    raw = result["details"].get("hermes_raw_callbacks", [])
    prompts = [row["rawInput"] for row in sorted(raw, key=lambda row: row["capturedAt"]) if row["hook"] == "pre_llm_call"]
    turns = [row["turn_id"] for row in prompts]
    spans = [row for export in report["exports"] for row in export.get("spans", [])]
    roots = [row for row in spans if row["attributes"].get("gen_ai.span.kind") == "CHAT"]
    by_turn = {row["attributes"]["gen_ai.chat.id"]: row for row in roots}
    requests = report["requests"]
    checks = {
        "native_processes_completed": result["exit_code"] == 0,
        "same_native_session": result["details"]["session_ids"] == [result["session_id"]] * 3,
        "three_native_user_turns": len(turns) == len(set(turns)) == len(roots) == 3,
        "observed_numbers": all(turn in by_turn and type(by_turn[turn]["attributes"].get("agentic.turn.number")) is int
            and by_turn[turn]["attributes"].get("agentic.turn.number") == index
            and by_turn[turn]["name"] == f"Turn {index}" for index, turn in enumerate(turns, 1)),
        "native_length_response": any(row.get("finish_reason") == "length" for row in requests),
        "native_internal_continuation": any(message.get("role") == "user" and message.get("content") == HERMES_LENGTH_CONTINUATION_PROMPT
            for row in requests for message in row["request"].get("messages", [])),
        "native_children_keep_parent": all(row["attributes"].get("gen_ai.chat.id") in by_turn
            and type(row["attributes"].get("agentic.turn.number")) is int
            and row["attributes"]["agentic.turn.number"] == by_turn[row["attributes"]["gen_ai.chat.id"]]["attributes"].get("agentic.turn.number")
            and row["parent_span_id"] == by_turn[row["attributes"]["gen_ai.chat.id"]]["span_id"]
            and row["trace_id"] == by_turn[row["attributes"]["gen_ai.chat.id"]]["trace_id"]
            for row in spans if row["attributes"].get("gen_ai.span.kind") in ("LLM", "TOOL")),
    }
    # Reuse the normal native oracle for per-physical-call usage/IO, graph,
    # nonzero original timestamps, resources, metrics, and session identity.
    from verify import verify_case
    ordinary = verify_case(app="hermes", scenario="multi-turn", expected_turns=3,
        model=report.get("model", "native-e2e-chat"), receipt=report["receipt"], result=result,
        requests=requests, exports=report["exports"], expected_resource=report.get("expected_resource"))
    checks.update(ordinary["checks"])
    generations = [row for row in spans if row["attributes"].get("gen_ai.span.kind") == "LLM"]
    before_rows = [row for row in raw if row["hook"] in ("pre_api_request", "pre_auxiliary_call")]
    after_rows = [row for row in raw if row["hook"] in ("post_api_request", "post_auxiliary_call")]
    before = {row["rawInput"].get("api_request_id"): row["rawInput"] for row in before_rows}
    after = {row["rawInput"].get("api_request_id"): row["rawInput"] for row in after_rows}
    api_ids = [row["attributes"].get("agentic.model_call.id") for row in generations]
    checks["native.api_ids"] = bool(api_ids) and len(api_ids) == len(set(api_ids)) and set(api_ids) == set(before)
    # This fixed host continues a length response before post_api_request.
    # Only that independently wire-verified request may lack its native end;
    # never silently waive terminals for normal or auxiliary requests.
    from verify import decoded
    length_ids = [row["attributes"].get("agentic.model_call.id") for row in generations
        if decoded(row["attributes"].get("gen_ai.response.finish_reasons")) == ["length"]]
    missing_terminal = set(before) - set(after)
    limited_length = (result.get("version") == "0.21.5" and all(ordinary["checks"].values())
        and len(length_ids) == 1 and missing_terminal == set(length_ids))
    checks["native.api_terminals"] = (len(before_rows) == len(before) and len(after_rows) == len(after)
        and None not in before and None not in after and set(after) <= set(before)
        and (not missing_terminal or limited_length))
    def source_matches(row):
        attrs = row["attributes"]
        native = before.get(attrs.get("agentic.model_call.id"), {})
        terminal = after.get(attrs.get("agentic.model_call.id"), {})
        def nano(value):
            return int(datetime.fromtimestamp(value, timezone.utc).timestamp() * 1_000_000_000)
        try:
            return (native.get("session_id") == result["session_id"]
                and attrs.get("gen_ai.conversation.id") == native["session_id"]
                and attrs.get("gen_ai.chat.id") == attrs.get("agentic.turn.id") == native.get("turn_id")
                and row["start_time_unix_nano"] == nano(native["started_at"])
                and ((not terminal and limited_length and attrs.get("agentic.model_call.id") in missing_terminal)
                    or (terminal.get("session_id") == native["session_id"]
                        and terminal.get("turn_id") == native.get("turn_id")
                        and row["end_time_unix_nano"] == nano(terminal["ended_at"]))))
        except (KeyError, TypeError, ValueError):
            return False
    checks["native.api_source"] = bool(generations) and all(source_matches(row) for row in generations)
    from hermes_call_evidence import call_checks
    checks.update(call_checks(report))
    return {"passed": all(checks.values()), "checks": checks, "native_turn_ids": turns,
            "observed_numbers": [by_turn[turn]["attributes"]["agentic.turn.number"] for turn in turns if turn in by_turn],
            "unverified_native_end_ids": sorted(missing_terminal) if limited_length else [],
            "compressed_history": "see hermes_compression.py; this probe does not trigger host compression"}


def main():
    from capture import Capture
    from fake_provider import FakeProvider
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtimes", type=Path, required=True, help="JSON app -> official CLI argv prefix")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    from run_suite import load_runtimes
    runtime = load_runtimes(args.runtimes, ["hermes"])["hermes"]
    args.out.mkdir(parents=True, exist_ok=False)
    workspace = args.out / "workspace"
    workspace.mkdir()
    receipt = workspace / "receipt.txt"
    receipt.write_text("receipt: native-turn-numbering\n")
    with Capture() as collector, FakeProvider(receipt, "multi-turn", length_continuation=True) as provider:
        context = CaseContext(repo=args.repo.resolve(), out=args.out / "run", workspace=workspace,
            runtime=tuple(runtime), runtime_version="0.21.5", scenario="multi-turn",
            provider_url=provider.url, collector_url=collector.url, prompt="Read receipt.txt and return its receipt.",
            followup_prompt="Read receipt.txt again and return its receipt.", timeout_seconds=180,
            resource={"ant.agent.type":"hermes", "ant.agent.product":"Hermes", "ant.agent.instance":"turn-numbering-test"})
        result = run_case(context, additional_prompts=("Read receipt.txt a third time and return its receipt.",))
    report = {"result":asdict(result), "requests":provider.requests, "exports":collector.exports,
              "receipt":"native-turn-numbering", "model":context.model, "expected_resource":context.resource}
    report["verification"] = assess(report)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["verification"], indent=2))
    return 0 if report["verification"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
