"""Portable deterministic RAG application for framework/SDK acceptance.

The chat policy is scripted, not language-model reasoning. Retrieval executes
LangChain's hash-seeded fake embeddings and real cosine nearest-neighbor search;
the query deliberately equals the target document, not a semantic paraphrase.
No exporter, credentials, network endpoint, or filesystem backend is configured.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import nullcontext
from copy import deepcopy
import json
import re
from threading import Lock
from typing import Any
from uuid import uuid4

from pydantic import Field
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding, Embeddings
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import StructuredTool, ToolException
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.vectorstores import InMemoryVectorStore


SCENARIOS = frozenset({"success", "no-results", "retrieval-error", "retry", "changed-corpus",
                      "zero-usage", "unknown-usage", "stream", "cancel", "tool-exception"})


def _json_content(message) -> Any:
    content = message.content
    if isinstance(content, list):
        content = "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)
    return json.loads(content)


def _message_record(message) -> dict:
    """Capture actual synthetic framework history, including child ToolMessages."""
    result = {"type": message.type, "content": deepcopy(message.content)}
    for key in ("name", "id", "tool_call_id", "status", "tool_calls", "usage_metadata"):
        value = getattr(message, key, None)
        if value is not None:
            result[key] = deepcopy(value)
    return result


class _ObservedEmbeddings(Embeddings):
    def __init__(self, fixture):
        self.fixture = fixture
        self.fake = DeterministicFakeEmbedding(size=16)

    def _call(self, op, inputs):
        # Observation is explicit application instrumentation, not automatic
        # LangChain embedding instrumentation. Preserve real numeric vectors.
        with self.fixture.observe_embedding(op, deepcopy(inputs)) as update:
            raw = getattr(self.fake, op)(inputs)
            output = [[float(v) for v in row] for row in raw] if op == "embed_documents" else [float(v) for v in raw]
            update(deepcopy(output))
        self.fixture.record("embedding", op=op, inputs=inputs, output=output,
                            dimensions=16, vector_count=len(output) if op == "embed_documents" else 1)
        return output

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._call("embed_documents", texts)

    def embed_query(self, text: str) -> list[float]:
        return self._call("embed_query", text)


class RAGFixture:
    def __init__(self, scenario="success", receipt=None, tenant="orion", observe_embedding=None):
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown fixture scenario: {scenario}")
        if not isinstance(tenant, str) or not tenant:
            raise ValueError("tenant must be a nonempty string")
        self.scenario = scenario
        self.tenant = tenant
        self.receipt = receipt if receipt is not None else "receipt-" + uuid4().hex
        self.ledger: list[dict] = []
        self.observe_embedding = observe_embedding or (lambda _op, _inputs: nullcontext(lambda _output: None))
        self.stream_started = asyncio.Event()
        self.release_stream = asyncio.Event()
        self._attempts = 0
        self._store = None
        self._index_lock = Lock()
        self._state_lock = Lock()
        days = 21 if scenario == "changed-corpus" else 14
        self._target_text = f"Storage policy for tenant {tenant}. Retention: {days} days. Daily storage: 10 GiB."
        self._documents = [
            Document(id=f"policy-{tenant}", page_content=self._target_text,
                     metadata={"tenant": tenant, "policy_id": f"policy-{tenant}"}),
            Document(id=f"policy-{tenant}-obsolete", page_content=f"Obsolete storage policy for tenant {tenant}. Retention: 7 days. Daily storage: 3 GiB.",
                     metadata={"tenant": tenant, "policy_id": f"policy-{tenant}-obsolete"}),
            Document(id="policy-vega", page_content="Vega audit policy. Retention: 30 days. Daily storage: 2 GiB.",
                     metadata={"tenant": "other-vega", "policy_id": "policy-vega"}),
        ]
        if scenario == "no-results":
            self._documents = [doc for doc in self._documents if doc.metadata["tenant"] != tenant]
        self._tools = self._make_tools()

    @property
    def expected(self):
        # An oracle convenience only. The model and tools never use this value.
        days = 21 if self.scenario == "changed-corpus" else 14
        return {"tenant": self.tenant, "retention_days": days, "daily_gib": 10,
                "storage_gib": days * 10, "citations": [f"policy-{self.tenant}"], "receipt": self.receipt}

    def record(self, kind, **fields):
        with self._state_lock:
            self.ledger.append({"kind": kind, **deepcopy(fields)})

    def _retriever(self):
        # Lazy construction keeps document embedding inside the real request
        # and its observation context, rather than doing hidden startup work.
        with self._index_lock:
            if self._store is None:
                self._store = InMemoryVectorStore(_ObservedEmbeddings(self))
                self._store.add_documents(self._documents)
        return self._store.as_retriever(search_kwargs={
            "k": 1, "filter": lambda document: document.metadata.get("tenant") == self.tenant,
        })

    def _make_tools(self):
        def retrieve_policy(tenant: str) -> dict:
            """Retrieve the tenant's policy from the deterministic vector index."""
            arguments = {"tenant": tenant}
            try:
                if tenant != self.tenant:
                    raise ToolException("Requested tenant is outside this fixture's scope")
                with self._state_lock:
                    self._attempts += 1
                    attempt = self._attempts
                # Inherit BaseTool's child callback context. An injected tool
                # config is its caller's context and would make the retriever
                # a sibling of this tool rather than a child.
                docs = self._retriever().invoke(self._target_text)
                self.record("retrieval", attempt=attempt, query=self._target_text, tenant=tenant,
                            document_ids=[doc.metadata["policy_id"] for doc in docs])
                if self.scenario == "tool-exception":
                    raise RuntimeError("Unhandled retrieval failure after actual vector search")
                if self.scenario == "retrieval-error" or (self.scenario == "retry" and attempt == 1):
                    raise ToolException("Controlled retrieval failure after actual vector search")
                result = {"tenant": tenant, "documents": [
                    {"id": doc.metadata["policy_id"], "text": doc.page_content} for doc in docs
                ], "receipt": self.receipt}
                self.record("tool_result", name="retrieve_policy", arguments=arguments, result=result)
                return result
            except (ToolException, RuntimeError) as error:
                self.record("tool_error", name="retrieve_policy", arguments=arguments, error=str(error))
                raise

        def calculate_storage(tenant: str, retention_days: int, daily_gib: int,
                              citations: list[str], receipt: str) -> dict:
            """Calculate retained storage using the actual retrieved policy fields."""
            arguments = {"tenant": tenant, "retention_days": retention_days, "daily_gib": daily_gib,
                         "citations": citations, "receipt": receipt}
            if tenant != self.tenant or receipt != self.receipt:
                error = "Calculation tenant or receipt does not match the actual retrieval"
                self.record("tool_error", name="calculate_storage", arguments=arguments, error=error)
                raise ToolException(error)
            if retention_days < 0 or daily_gib < 0:
                raise ToolException("Storage inputs cannot be negative")
            result = {"tenant": tenant, "retention_days": retention_days, "daily_gib": daily_gib,
                      "storage_gib": retention_days * daily_gib, "citations": list(citations), "receipt": receipt}
            self.record("tool_result", name="calculate_storage", arguments=arguments, result=result)
            return result

        return [StructuredTool.from_function(retrieve_policy, handle_tool_error=True),
                StructuredTool.from_function(calculate_storage, handle_tool_error=True)]

    def tools(self):
        return list(self._tools)

    def model(self, role="worker"):
        if role not in {"worker", "parent"}:
            raise ValueError("role must be worker or parent")
        return _PolicyChat(messages=iter(()), fixture=self, role=role)

    def usage(self):
        if self.scenario == "unknown-usage":
            return None
        if self.scenario == "zero-usage":
            return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}


class _PolicyChat(GenericFakeChatModel):
    """Thin GenericFake adapter with a message-driven, explicit test policy."""
    fixture: Any = Field(exclude=True)
    role: str = "worker"
    model_name: str = "e2e-fake-rag-chat"

    @property
    def _identifying_params(self):
        return {"model_name": self.model_name, "role": self.role}

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools],
                         tool_choice=tool_choice, **kwargs)

    def _plan(self, messages, tools=None):
        names = {tool["function"]["name"] for tool in tools} if tools is not None else None
        last_human = next((i for i in range(len(messages) - 1, -1, -1)
                           if isinstance(messages[i], HumanMessage)), -1)
        prompt = str(messages[last_human].content).lower() if last_human >= 0 else ""
        if any(word in prompt for word in ("follow up", "follow-up", "followup", "previous")):
            for message in reversed(messages[:last_human]):
                if isinstance(message, AIMessage) and not message.tool_calls and message.content:
                    try:
                        previous = _json_content(message)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(previous, dict) and ("storage_gib" in previous or "error" in previous):
                        return self._answer(previous)
            raise ValueError("Follow-up has no checkpointed final answer")

        recent = messages[last_human + 1:]
        last_tool = next((message for message in reversed(recent) if isinstance(message, ToolMessage)), None)
        if last_tool is not None:
            tool_name = last_tool.name
            if not tool_name:
                # Relay's official message codec omits ToolMessage.name.
                # Correlate the actual result with the actual preceding call;
                # keep the message and ledger unchanged for independent audit.
                tool_name = next((call["name"] for message in reversed(recent)
                                  if isinstance(message, AIMessage)
                                  for call in message.tool_calls
                                  if call["id"] == last_tool.tool_call_id), None)
            controlled_error = (tool_name == "retrieve_policy" and last_tool.content ==
                                "Controlled retrieval failure after actual vector search")
            if last_tool.status == "error" or controlled_error:
                if self.fixture.scenario == "retry" and tool_name == "retrieve_policy":
                    return self._call("retrieve_policy", {"tenant": self.fixture.tenant}, names)
                return self._answer({"error": "retrieval_error", "tenant": self.fixture.tenant,
                                     "message": str(last_tool.content)})
            data = _json_content(last_tool)
            if tool_name in {"calculate_storage", "task"}:
                # Preserve the real ToolMessage result, including a child
                # agent's actual answer; never source answers from expected.
                return self._answer(data)
            if tool_name == "retrieve_policy":
                documents = data["documents"]
                if not documents:
                    return self._answer({"error": "no_results", "tenant": data["tenant"],
                                         "receipt": data["receipt"]})
                document = documents[0]
                days = re.search(r"Retention: (\d+) days", document["text"])
                daily = re.search(r"Daily storage: (\d+) GiB", document["text"])
                if days is None or daily is None:
                    raise ValueError("Retrieved policy lacks required numeric fields")
                return self._call("calculate_storage", {
                    "tenant": data["tenant"], "retention_days": int(days[1]), "daily_gib": int(daily[1]),
                    "citations": [document["id"]], "receipt": data["receipt"],
                }, names)
        if self.role == "parent" and names is not None and "task" in names:
            return self._call("task", {"subagent_type": "researcher", "description":
                f"Retrieve the storage policy for tenant {self.fixture.tenant}, calculate storage, "
                "and return the exact calculation JSON with citations and receipt."}, names)
        return self._call("retrieve_policy", {"tenant": self.fixture.tenant}, names)

    def _answer(self, result):
        return AIMessage(content=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                         usage_metadata=self.fixture.usage(), response_metadata={
                             "model_name": self.model_name, "finish_reason": "stop"})

    def _call(self, name, arguments, names):
        if names is not None and name not in names:
            raise ValueError(f"Fixture attempted an unbound tool: {name}")
        return AIMessage(content="", tool_calls=[{"name": name, "args": arguments,
                          "id": "call-" + uuid4().hex, "type": "tool_call"}],
                         usage_metadata=self.fixture.usage(), response_metadata={
                             "model_name": self.model_name, "finish_reason": "tool_calls"})

    def _begin(self, messages, run_manager):
        call_id = str(getattr(run_manager, "run_id", None) or uuid4())
        self.fixture.record("model_started", call_id=call_id, role=self.role, model_name=self.model_name,
                            input_messages=[_message_record(message) for message in messages])
        return call_id

    def _complete(self, call_id, message):
        self.fixture.record("model_completed", call_id=call_id, role=self.role, model_name=self.model_name,
                            content=message.content, tool_calls=message.tool_calls, usage=message.usage_metadata)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        call_id = self._begin(messages, run_manager)
        message = self._plan(messages, kwargs.get("tools"))
        self._complete(call_id, message)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _chunks(self, message):
        for index, tool in enumerate(message.tool_calls):
            yield AIMessageChunk(content="", tool_call_chunks=[{
                "index": index, "name": tool["name"], "id": tool["id"],
                "args": json.dumps(tool["args"], ensure_ascii=False),
            }])
        for offset in range(0, len(message.content), 16):
            yield AIMessageChunk(content=message.content[offset:offset + 16])
        # Usage appears only after successful completion, once per model call.
        yield AIMessageChunk(content="", chunk_position="last", usage_metadata=message.usage_metadata,
                             response_metadata=message.response_metadata)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]:
        if self.fixture.scenario == "cancel":
            raise ValueError("The cancellation fixture requires asynchronous streaming")
        call_id = self._begin(messages, run_manager)
        message = self._plan(messages, kwargs.get("tools"))
        for chunk in self._chunks(message):
            if chunk.content:
                self.fixture.record("model_partial", call_id=call_id, role=self.role, text=chunk.content)
            yield ChatGenerationChunk(message=chunk)
        self._complete(call_id, message)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        call_id = self._begin(messages, run_manager)
        message = self._plan(messages, kwargs.get("tools"))
        paused = False
        try:
            for chunk in self._chunks(message):
                if chunk.content:
                    self.fixture.record("model_partial", call_id=call_id, role=self.role, text=chunk.content)
                yield ChatGenerationChunk(message=chunk)
                if chunk.content and not paused and self.fixture.scenario == "cancel":
                    paused = True
                    self.fixture.stream_started.set()
                    await self.fixture.release_stream.wait()
                await asyncio.sleep(0)
            self._complete(call_id, message)
        except asyncio.CancelledError as error:
            self.fixture.record("model_cancelled", call_id=call_id, role=self.role,
                                exception_type=type(error).__name__)
            raise
        except GeneratorExit as error:
            # A request cancellation can close this generator while an outer
            # token callback consumes its yield, before release_stream.wait().
            # Preserve the observed close type; an unrelated early close must
            # not be relabelled as the fixture's requested cancellation.
            if self.fixture.scenario == "cancel":
                self.fixture.record("model_cancelled", call_id=call_id, role=self.role,
                                    exception_type=type(error).__name__)
            raise
