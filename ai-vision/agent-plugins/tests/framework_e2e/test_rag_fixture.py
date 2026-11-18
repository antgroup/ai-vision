"""Independent business literals and real framework execution, without exporters."""
import asyncio
from contextlib import contextmanager
import json
import math
import unittest

from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from rag_fixture import RAGFixture


PROMPT = "Retrieve the orion policy and calculate storage for 10 GiB per day."
FOLLOWUP = "Follow up: return the previous answer with the same receipt without tools."


def build(fixture):
    return create_agent(model=fixture.model(), tools=fixture.tools(), checkpointer=InMemorySaver())


def answer(result):
    return json.loads(result["messages"][-1].content)


class FixtureBusinessTests(unittest.TestCase):
    def test_real_retriever_is_child_of_retrieve_tool(self):
        runs = []
        class AncestryRecorder(BaseCallbackHandler):
            def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
                runs.append(("tool", serialized["name"], run_id, parent_run_id))
            def on_retriever_start(self, serialized, query, *, run_id, parent_run_id=None, **kwargs):
                runs.append(("retriever", None, run_id, parent_run_id))
        fixture = RAGFixture()
        build(fixture).invoke({"messages": [HumanMessage(PROMPT)]}, {
            "configurable": {"thread_id": "ancestry"}, "callbacks": [AncestryRecorder()],
        })
        tool_id = next(row[2] for row in runs if row[:2] == ("tool", "retrieve_policy"))
        parents = [row[3] for row in runs if row[0] == "retriever"]
        self.assertEqual(parents, [tool_id])

    def test_real_retrieval_and_calculation_match_literal_business_contract(self):
        fixture = RAGFixture(receipt="independent-receipt")
        result = build(fixture).invoke({"messages": [HumanMessage(PROMPT)]},
                                      {"configurable": {"thread_id": "business"}})
        self.assertEqual(answer(result), {
            "tenant": "orion", "retention_days": 14, "daily_gib": 10,
            "storage_gib": 140, "citations": ["policy-orion"], "receipt": "independent-receipt",
        })
        tools = [row for row in fixture.ledger if row["kind"] == "tool_result"]
        self.assertEqual([row["name"] for row in tools], ["retrieve_policy", "calculate_storage"])
        self.assertEqual(tools[1]["arguments"]["receipt"], tools[0]["result"]["receipt"])
        embedding = [row for row in fixture.ledger if row["kind"] == "embedding"]
        self.assertEqual([row["op"] for row in embedding], ["embed_documents", "embed_query"])
        self.assertEqual(embedding[0]["vector_count"], 3)
        self.assertEqual(len(embedding[0]["output"]), 3)
        self.assertTrue(all(len(vector) == 16 for vector in embedding[0]["output"]))
        self.assertEqual(len(embedding[1]["output"]), 16)
        self.assertEqual(embedding[0]["output"][0], embedding[1]["output"])
        self.assertEqual([row["document_ids"] for row in fixture.ledger if row["kind"] == "retrieval"],
                         [["policy-orion"]])

    def test_vector_ranking_has_multiple_eligible_documents(self):
        fixture = RAGFixture()
        result = fixture.tools()[0].invoke({"tenant": "orion"})
        indexed = next(row for row in fixture.ledger if row["kind"] == "embedding" and row["op"] == "embed_documents")
        queried = next(row for row in fixture.ledger if row["kind"] == "embedding" and row["op"] == "embed_query")
        self.assertEqual(sum("tenant orion" in text for text in indexed["inputs"]), 2)
        self.assertIn("obsolete", indexed["inputs"][1].lower())
        query = queried["output"]
        similarities = [sum(a * b for a, b in zip(query, vector)) /
                        math.sqrt(sum(v * v for v in query) * sum(v * v for v in vector))
                        for vector in indexed["output"]]
        self.assertAlmostEqual(similarities[0], 1.0)
        self.assertGreater(similarities[0], similarities[1])
        self.assertEqual([doc["id"] for doc in result["documents"]], ["policy-orion"])

    def test_unhandled_tool_exception_propagates_from_real_agent(self):
        fixture = RAGFixture(scenario="tool-exception")
        with self.assertRaisesRegex(RuntimeError, "Unhandled retrieval failure"):
            build(fixture).invoke({"messages": [HumanMessage(PROMPT)]},
                                  {"configurable": {"thread_id": "unhandled"}})
        self.assertEqual(len([r for r in fixture.ledger if r["kind"] == "retrieval"]), 1)
        self.assertEqual(len([r for r in fixture.ledger if r["kind"] == "embedding"]), 2)
        self.assertTrue(any(r["kind"] == "tool_error" and r["name"] == "retrieve_policy" for r in fixture.ledger))
        self.assertFalse(any(r["kind"] == "tool_result" for r in fixture.ledger))

    def test_changed_document_changes_answer_to_210(self):
        fixture = RAGFixture(scenario="changed-corpus", receipt="changed-receipt")
        result = build(fixture).invoke({"messages": [HumanMessage(PROMPT)]},
                                      {"configurable": {"thread_id": "changed"}})
        self.assertEqual(answer(result), {
            "tenant": "orion", "retention_days": 21, "daily_gib": 10,
            "storage_gib": 210, "citations": ["policy-orion"], "receipt": "changed-receipt",
        })

    def test_model_copies_actual_calculation_tool_message_not_fixture_expected(self):
        fixture = RAGFixture(receipt="must-not-leak")
        actual = {"tenant": "orion", "retention_days": 9, "daily_gib": 8,
                  "storage_gib": 72, "citations": ["different-document"], "receipt": "from-tool"}
        response = fixture.model().invoke([HumanMessage(PROMPT), ToolMessage(
            content=json.dumps(actual), name="calculate_storage", tool_call_id="literal-call")])
        self.assertEqual(json.loads(response.content), actual)

    def test_nameless_tool_message_uses_matching_real_call_without_mutating_history(self):
        fixture = RAGFixture(receipt="must-not-leak")
        actual = {"tenant": "orion", "retention_days": 9, "daily_gib": 8,
                  "storage_gib": 72, "citations": ["different-document"], "receipt": "from-tool"}
        history = [HumanMessage(PROMPT), AIMessage(content="", tool_calls=[
            {"name": "calculate_storage", "args": {}, "id": "calculation-id"},
            {"name": "retrieve_policy", "args": {}, "id": "different-id"},
        ]), ToolMessage(content=json.dumps(actual), tool_call_id="calculation-id")]
        response = fixture.model().invoke(history)
        self.assertEqual(response.tool_calls, [])
        self.assertEqual(json.loads(response.content), actual)
        self.assertIsNone(history[-1].name)
        started = next(row for row in fixture.ledger if row["kind"] == "model_started")
        self.assertNotIn("name", started["input_messages"][-1])

    def test_relay_missing_status_uses_actual_controlled_error_content(self):
        for scenario in ("retry", "retrieval-error"):
            with self.subTest(scenario=scenario):
                fixture = RAGFixture(scenario=scenario)
                history = [HumanMessage(PROMPT), AIMessage(content="", tool_calls=[
                    {"name": "retrieve_policy", "args": {"tenant": "orion"}, "id": "failed-id"}
                ]), ToolMessage(content="Controlled retrieval failure after actual vector search",
                                tool_call_id="failed-id")]
                try:
                    response = fixture.model().invoke(history)
                except ValueError as error:
                    self.fail(f"Actual controlled tool error must remain interpretable: {error}")
                if scenario == "retry":
                    self.assertEqual(response.tool_calls[0]["name"], "retrieve_policy")
                else:
                    self.assertEqual(json.loads(response.content)["error"], "retrieval_error")

    def test_followup_uses_checkpoint_without_repeating_tools(self):
        fixture = RAGFixture(receipt="checkpoint-receipt")
        agent = build(fixture)
        config = {"configurable": {"thread_id": "same-thread"}}
        first = agent.invoke({"messages": [HumanMessage(PROMPT)]}, config)
        before = len([row for row in fixture.ledger if row["kind"] == "tool_result"])
        second = agent.invoke({"messages": [HumanMessage(FOLLOWUP)]}, config)
        self.assertEqual(answer(second), answer(first))
        self.assertEqual(before, 2)
        self.assertEqual(len([row for row in fixture.ledger if row["kind"] == "tool_result"]), 2)

    def test_no_results_and_real_tool_exception_are_not_success(self):
        for scenario, error in (("no-results", "no_results"), ("retrieval-error", "retrieval_error")):
            with self.subTest(scenario=scenario):
                fixture = RAGFixture(scenario=scenario)
                result = build(fixture).invoke({"messages": [HumanMessage(PROMPT)]},
                                              {"configurable": {"thread_id": scenario}})
                self.assertEqual(answer(result)["error"], error)
                self.assertNotIn("storage_gib", answer(result))
                self.assertFalse(any(row.get("name") == "calculate_storage" for row in fixture.ledger))
                self.assertTrue(any(row["kind"] == "embedding" for row in fixture.ledger))
        self.assertTrue(any(row["kind"] == "tool_error" for row in fixture.ledger))

    def test_retry_occurs_only_after_real_failed_tool_message(self):
        fixture = RAGFixture(scenario="retry", receipt="retry-receipt")
        result = build(fixture).invoke({"messages": [HumanMessage(PROMPT)]},
                                      {"configurable": {"thread_id": "retry"}})
        self.assertEqual(answer(result)["storage_gib"], 140)
        self.assertEqual(answer(result)["receipt"], "retry-receipt")
        self.assertEqual(len([r for r in fixture.ledger if r["kind"] == "tool_error"]), 1)
        self.assertEqual(len([r for r in fixture.ledger if r["kind"] == "retrieval"]), 2)
        failures = [m for m in result["messages"] if m.type == "tool" and m.status == "error"]
        self.assertEqual(len(failures), 1)

    def test_usage_zero_and_unknown_remain_distinct(self):
        for scenario, usage in (("zero-usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}),
                                ("unknown-usage", None)):
            fixture = RAGFixture(scenario=scenario)
            result = build(fixture).invoke({"messages": [HumanMessage(PROMPT)]},
                                          {"configurable": {"thread_id": scenario}})
            self.assertEqual(result["messages"][-1].usage_metadata, usage)
            completed = [r for r in fixture.ledger if r["kind"] == "model_completed"]
            self.assertEqual(len(completed), 3)
            self.assertTrue(all(r["usage"] == usage for r in completed))

    def test_observer_receives_actual_vectors_and_is_lazy(self):
        observed = []
        @contextmanager
        def observer(op, inputs):
            record = {"op": op, "inputs": inputs}
            observed.append(record)
            yield lambda output: record.update(output=output)
        fixture = RAGFixture(observe_embedding=observer)
        self.assertEqual(observed, [])
        tool = fixture.tools()[0]
        tool.invoke({"tenant": "orion"})
        self.assertEqual([r["op"] for r in observed], ["embed_documents", "embed_query"])
        self.assertEqual(observed[0]["output"][0], observed[1]["output"])
        self.assertEqual(len(observed[1]["output"]), 16)

    def test_tenant_scope_and_unknown_tool_binding_are_enforced(self):
        fixture = RAGFixture()
        reply = fixture.tools()[0].invoke({"tenant": "foreign"})
        self.assertIn("tenant", str(reply).lower())
        with self.assertRaises(ValueError):
            fixture.model().bind_tools([fixture.tools()[1]]).invoke([HumanMessage(PROMPT)])

    def test_deepagents_real_researcher_and_parent_pass_through(self):
        fixture = RAGFixture(receipt="researcher-receipt")
        agent = create_deep_agent(
            model=fixture.model(role="parent"), tools=[],
            subagents=[{"name": "researcher", "description": "Retrieve policy and calculate storage.",
                        "system_prompt": "Use the actual retrieval and calculation results.",
                        "model": fixture.model(role="worker"), "tools": fixture.tools()}],
            checkpointer=InMemorySaver(), name="storage-orchestrator")
        result = agent.invoke({"messages": [HumanMessage(PROMPT)]},
                              {"configurable": {"thread_id": "deepagents"}})
        self.assertEqual(answer(result)["storage_gib"], 140)
        self.assertEqual(answer(result)["receipt"], "researcher-receipt")
        completed = [r for r in fixture.ledger if r["kind"] == "model_completed"]
        self.assertEqual([r["role"] for r in completed], ["parent", "worker", "worker", "worker", "parent"])
        self.assertEqual(completed[0]["tool_calls"][0]["args"]["subagent_type"], "researcher")
        self.assertEqual(json.loads(completed[-1]["content"]), json.loads(completed[-2]["content"]))


class FixtureAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_fixture_closed_at_partial_yield_records_actual_generator_exit(self):
        fixture = RAGFixture(scenario="cancel")
        # The framework can close a model while its token callback is still
        # consuming the yielded text, before the fixture resumes its wait.
        stream = fixture.model()._astream([
            HumanMessage(PROMPT),
            ToolMessage(content='{"tenant":"orion","storage_gib":140,"receipt":"actual-tool"}',
                        name="calculate_storage", tool_call_id="calculation-id"),
        ])
        first = await anext(stream)
        self.assertTrue(first.message.content)
        self.assertIsNone(first.message.usage_metadata)
        self.assertFalse(fixture.stream_started.is_set())
        await stream.aclose()
        await stream.aclose()
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)
        started = [r for r in fixture.ledger if r["kind"] == "model_started"]
        partial = [r for r in fixture.ledger if r["kind"] == "model_partial"]
        cancelled = [r for r in fixture.ledger if r["kind"] == "model_cancelled"]
        self.assertEqual(len(started), 1)
        self.assertEqual(len(partial), 1)
        self.assertEqual(cancelled, [{
            "kind": "model_cancelled", "call_id": started[0]["call_id"],
            "role": "worker", "exception_type": "GeneratorExit",
        }])
        self.assertEqual(partial[0]["call_id"], cancelled[0]["call_id"])
        self.assertFalse(any(r["kind"] == "model_completed" for r in fixture.ledger))

    async def test_unrequested_model_close_does_not_claim_request_cancellation(self):
        fixture = RAGFixture(scenario="stream")
        stream = fixture.model()._astream([
            HumanMessage(PROMPT),
            ToolMessage(content='{"storage_gib":140}', name="calculate_storage",
                        tool_call_id="calculation-id"),
        ])
        await anext(stream)
        await stream.aclose()
        self.assertEqual([r["kind"] for r in fixture.ledger], ["model_started", "model_partial"])

    async def test_stream_emits_real_tools_then_text_and_final_usage_once_per_call(self):
        fixture = RAGFixture(scenario="stream", receipt="stream-receipt")
        chunks = []
        async for message, _ in build(fixture).astream(
                {"messages": [HumanMessage(PROMPT)]}, {"configurable": {"thread_id": "stream"}},
                stream_mode="messages", version="v1"):
            chunks.append(message)
        self.assertTrue(any(getattr(c, "tool_call_chunks", None) for c in chunks))
        self.assertEqual(len([c for c in chunks if isinstance(c, AIMessageChunk) and c.usage_metadata is not None]), 3)
        self.assertEqual(json.loads("".join(c.content for c in chunks if isinstance(c, AIMessageChunk) and isinstance(c.content, str)))["receipt"],
                         "stream-receipt")
        self.assertEqual(len([r for r in fixture.ledger if r["kind"] == "model_completed"]), 3)

    async def test_cancel_after_text_keeps_partial_without_final_usage(self):
        fixture = RAGFixture(scenario="cancel")
        chunks = []
        async def consume():
            async for message, _ in build(fixture).astream(
                    {"messages": [HumanMessage(PROMPT)]}, {"configurable": {"thread_id": "cancel"}},
                    stream_mode="messages", version="v1"):
                chunks.append(message)
        task = asyncio.create_task(consume())
        await asyncio.wait_for(fixture.stream_started.wait(), 30)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(any(r["kind"] == "model_partial" and r["text"] for r in fixture.ledger))
        self.assertEqual(len([r for r in fixture.ledger if r["kind"] == "model_completed"]), 2)
        self.assertEqual(len([r for r in fixture.ledger if r["kind"] == "model_cancelled"]), 1)
        cancelled = next(r for r in fixture.ledger if r["kind"] == "model_cancelled")
        self.assertEqual(cancelled["exception_type"], "CancelledError")
        self.assertEqual(len([c for c in chunks if isinstance(c, AIMessageChunk) and c.usage_metadata is not None]), 2)

    async def test_concurrent_fixtures_do_not_share_receipts_or_state(self):
        fixtures = [RAGFixture(receipt="concurrent-a"), RAGFixture(receipt="concurrent-b")]
        results = await asyncio.gather(*[
            build(f).ainvoke({"messages": [HumanMessage(PROMPT)]}, {"configurable": {"thread_id": str(i)}})
            for i, f in enumerate(fixtures)])
        self.assertEqual([answer(r)["receipt"] for r in results], ["concurrent-a", "concurrent-b"])
        self.assertTrue(all(len([r for r in f.ledger if r["kind"] == "model_completed"]) == 3 for f in fixtures))


if __name__ == "__main__":
    unittest.main()
