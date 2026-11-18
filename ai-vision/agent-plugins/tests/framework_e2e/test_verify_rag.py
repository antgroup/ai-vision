"""Literal business oracle and mutation tests; no fixture/server oracle imported."""
import copy
import json
import unittest
from unittest.mock import patch

from verify_rag import compare_native_spans, compare_source_fields, verify_business, verify_inventory, usage_totals, verify_source_report, verify_platform_report, canonical_usage, observation_type, span_usage, reported_usage, usage_by_type, projected_metadata


def business(phase="initial"):
    days, storage = (21, 210) if phase == "changed-corpus" else (14, 140)
    result = {"tenant": "orion", "retention_days": days, "daily_gib": 10,
              "storage_gib": storage, "citations": ["policy-orion"], "receipt": "receipt-391"}
    retrieval = {"docs": [{"id": "policy-orion", "tenant": "orion", "retention_days": days, "daily_gib": 10}], "receipt": "receipt-391"}
    calculation = {"storage_gib": storage, "receipt": "receipt-391"}
    tools = [{"name": "retrieve_policy", "tool_call_id": "r1", "arguments": {"tenant": "orion"}, "output": retrieval},
             {"name": "calculate_storage", "tool_call_id": "c1", "arguments": {"retention_days": days, "daily_gib": 10, "receipt": "receipt-391"}, "output": calculation}]
    messages = [{"type": "human", "content": "What is Orion storage?"},
                {"type": "ai", "content": "", "tool_calls": [{"id": "r1", "name": "retrieve_policy", "args": {"tenant": "orion"}}]},
                {"type": "tool", "name": "retrieve_policy", "tool_call_id": "r1", "content": json.dumps(retrieval)},
                {"type": "ai", "content": "", "tool_calls": [{"id": "c1", "name": "calculate_storage", "args": tools[1]["arguments"]}]},
                {"type": "tool", "name": "calculate_storage", "tool_call_id": "c1", "content": json.dumps(calculation)},
                {"type": "ai", "content": json.dumps(result)}]
    return result, {"tools": tools, "messages": messages}


def spans():
    return [{"trace_id": "a" * 32, "span_id": "1" * 16, "parent_span_id": None, "name": "rag.request", "status": {"code": "OK"}, "attributes": {"langfuse.observation.type": "AGENT"}},
            {"trace_id": "a" * 32, "span_id": "2" * 16, "parent_span_id": "1" * 16, "name": "retrieve_policy", "status": {"code": "OK"}, "attributes": {"langfuse.observation.type": "TOOL"}},
            {"trace_id": "a" * 32, "span_id": "3" * 16, "parent_span_id": "2" * 16, "name": "embed.documents", "status": {"code": "OK"}, "attributes": {"langfuse.observation.type": "EMBEDDING"}},
            {"trace_id": "a" * 32, "span_id": "4" * 16, "parent_span_id": "1" * 16, "name": "calculate_storage", "status": {"code": "OK"}, "attributes": {"langfuse.observation.type": "TOOL"}},
            {"trace_id": "a" * 32, "span_id": "5" * 16, "parent_span_id": "1" * 16, "name": "chat", "status": {"code": "OK"}, "attributes": {"langfuse.observation.type": "GENERATION", "gen_ai.usage.input_tokens": 13, "gen_ai.usage.output_tokens": 5, "gen_ai.usage.total_tokens": 18}}]


class BusinessOracleTests(unittest.TestCase):
    def test_child_toolmessage_name_comes_from_real_tool_call_id(self):
        result, ledger = business()
        for message in ledger["messages"]:
            if message["type"] == "tool": message.pop("name")
        self.assertEqual(verify_business(result, ledger, "initial"), [])
        ledger["messages"][2]["tool_call_id"] = "invented"
        self.assertTrue(verify_business(result, ledger, "initial"))
    def test_literal_retrieval_calculation_final_dependency(self):
        result, ledger = business()
        self.assertEqual(verify_business(result, ledger, "initial"), [])

    def test_changed_corpus_is_independently_21_days_210_gb(self):
        result, ledger = business("changed-corpus")
        self.assertEqual(verify_business(result, ledger, "changed-corpus"), [])
        result["storage_gib"] = 140
        self.assertTrue(verify_business(result, ledger, "changed-corpus"))

    def test_missing_document_receipt_tool_or_toolmessage_fails(self):
        for mutation in ("document", "receipt", "tool", "toolmessage", "parent_input"):
            result, ledger = business()
            if mutation == "document": ledger["tools"][0]["output"]["docs"] = []
            elif mutation == "receipt": result.pop("receipt")
            elif mutation == "tool": ledger["tools"].pop()
            elif mutation == "toolmessage": ledger["messages"].pop(2)
            else: ledger["tools"][1]["arguments"]["receipt"] = "fabricated"
            with self.subTest(mutation=mutation): self.assertTrue(verify_business(result, ledger, "initial"))

    def test_answer_cannot_pass_without_calculator_result_or_observed_final(self):
        result, ledger = business()
        ledger["tools"][1]["output"]["storage_gib"] = 139
        self.assertTrue(verify_business(result, ledger, "initial"))
        result, ledger = business()
        ledger["messages"][-1]["content"] = "invented answer"
        self.assertTrue(verify_business(result, ledger, "initial"))


class SpanOracleTests(unittest.TestCase):
    def test_metadata_preserves_sdk_version_specific_json_strings(self):
        prefix = 'langfuse.observation.metadata'
        source = {'scope_name': 'langfuse-sdk', 'scope_version': '5.0.2',
                  'resource': {'telemetry.sdk.language': 'python'}}
        attrs = {prefix + '.numeric_string': '\"42\"', prefix + '.boolean': 'true',
                 prefix + '.object': '{"a":1}', prefix + '.array': '["x",2]',
                 prefix + '.quoted': '\"\\\"quoted\\\"\"', prefix + '.empty': '\"\"'}
        self.assertEqual(projected_metadata(attrs, prefix, source),
                         {'numeric_string': '42', 'boolean': True, 'object': {'a': 1},
                          'array': ['x', 2], 'quoted': '"quoted"', 'empty': ''})
        old = {**source, 'scope_version': '4.16.0'}
        legacy = {prefix + '.numeric_string': '42', prefix + '.boolean_string': 'true',
                  prefix + '.null_string': 'null', prefix + '.object': '{"a":1}'}
        self.assertEqual(projected_metadata(legacy, prefix, old),
                         {'numeric_string': '42', 'boolean_string': 'true',
                          'null_string': 'null', 'object': '{"a":1}'})
        # Rows with no recorded SDK provenance retain the existing projection.
        self.assertEqual(projected_metadata(legacy, prefix),
                         {'numeric_string': 42, 'boolean_string': True,
                          'null_string': None, 'object': {'a': 1}})

    def test_platform_source_fields_require_current_user_and_absent_reserved_user(self):
        source=spans()[0]
        source['attributes'].update({'user.id':'synthetic-user','gen_ai.user.id':'synthetic-user','langfuse.user.id':'synthetic-user'})
        target={'space_id':'space-public','agent_uuid':'agent-public','agent_product':'framework','authenticated_user_id':'authenticated-framework-user'}
        actual=copy.deepcopy(source); actual['attributes'].pop('user.id')
        actual['resource']={"aivision."+field:target[key] for field,key in (('space.id','space_id'),('agent.id','agent_uuid'),('agent.product','agent_product'),('user.id','authenticated_user_id'))}
        self.assertEqual(compare_source_fields(source,actual,target),[])
        for value in ('missing','foreign',None,True,1):
            with self.subTest(user=value):
                changed=copy.deepcopy(actual)
                if value=='missing': changed['resource'].pop('aivision.user.id')
                else: changed['resource']['aivision.user.id']=value
                self.assertTrue(compare_source_fields(source,changed,target))
        for value in ('synthetic-user',None,'foreign'):
            with self.subTest(residue=value):
                changed=copy.deepcopy(actual); changed['attributes']['user.id']=value
                self.assertTrue(compare_source_fields(source,changed,target))
        source['attributes'].pop('user.id')
        changed=copy.deepcopy(actual); changed['attributes']['user.id']=None
        self.assertTrue(compare_source_fields(source,changed,target))

    def test_platform_scope_types_and_length_cannot_be_coerced(self):
        source=spans()[0]; actual=copy.deepcopy(source)
        target={'space_id':'space-public','agent_uuid':'agent-public','agent_product':'framework','authenticated_user_id':'u'*32}
        actual['resource']={"aivision."+field:target[key] for field,key in (('space.id','space_id'),('agent.id','agent_uuid'),('agent.product','agent_product'),('user.id','authenticated_user_id'))}
        for field,key in (('space.id','space_id'),('agent.id','agent_uuid'),('agent.product','agent_product'),('user.id','authenticated_user_id')):
            for value in ('missing',None,True,1,'foreign'):
                with self.subTest(field=field,value=value):
                    changed=copy.deepcopy(actual)
                    if value=='missing': changed['resource'].pop('aivision.'+field)
                    else: changed['resource']['aivision.'+field]=value
                    self.assertTrue(compare_source_fields(source,changed,target))
            changed=copy.deepcopy(actual); scoped={**target,key:'1'}; changed['resource']['aivision.'+field]=1
            self.assertTrue(compare_source_fields(source,changed,scoped))
        for user in ('u'*32,'u'*36,'u'*37):
            changed=copy.deepcopy(actual); changed['resource']['aivision.user.id']=user
            self.assertEqual(not compare_source_fields(source,changed,{**target,'authenticated_user_id':user}),len(user)<=36)

    def test_sdk_source_only_does_not_require_platform_scope_or_reserved_strip(self):
        source=spans()[0]
        source['attributes'].update({'user.id':'sdk-user','gen_ai.user.id':'sdk-user','langfuse.user.id':'sdk-user'})
        actual=copy.deepcopy(source)
        self.assertEqual(compare_source_fields(source,actual,target=None),[])
        self.assertTrue(compare_source_fields(source,actual,target={}))

    def test_platform_trusted_resource_must_be_a_dictionary(self):
        source=spans()[0]; source['resource']={'service.name':'synthetic-service'}
        target={'space_id':'space-public','agent_uuid':'agent-public','agent_product':'framework',
                'authenticated_user_id':'authenticated-public-user'}
        actual=copy.deepcopy(source)
        actual['resource'].update({'aivision.space.id':target['space_id'],
            'aivision.agent.id':target['agent_uuid'],'aivision.agent.product':target['agent_product'],
            'aivision.user.id':target['authenticated_user_id']})
        self.assertEqual(compare_source_fields(source,actual,target),[])
        for resource in (None, [], True):
            with self.subTest(resource=resource):
                changed=copy.deepcopy(actual); changed['resource']=resource
                self.assertTrue(compare_source_fields(source,changed,target))

    def test_native_payload_nanoseconds_and_application_user_alias_are_exact(self):
        source = spans()[0]
        source.update(start_time_unix_nano=123456789, end_time_unix_nano=223456789)
        source["attributes"].update({"user.id": "synthetic-user", "business.receipt": "r-391"})
        source["resource"] = {"service.name": "rag-fixture"}
        actual = copy.deepcopy(source)
        actual.update(start_time_unix_nano="1970-01-01T00:00:00.123456789Z", end_time_unix_nano="1970-01-01T00:00:00.223456789Z", payload={"start_time_unix_nano": 123456789, "end_time_unix_nano": 223456789})
        actual["attributes"].pop("user.id")
        actual["attributes"].update({"langfuse.user.id": "synthetic-user", "gen_ai.user.id": "synthetic-user"})
        self.assertEqual(compare_native_spans([source], [actual]), [])
        self.assertEqual(compare_source_fields(source, actual), [])
        for mutation in ("nano", "alias", "business", "service"):
            wrong = copy.deepcopy(actual)
            if mutation == "nano": wrong["payload"]["start_time_unix_nano"] += 1
            elif mutation == "alias": wrong["attributes"]["gen_ai.user.id"] = "different-user"
            elif mutation == "business": wrong["attributes"].pop("business.receipt")
            else: wrong["resource"]["service.name"] = "different-service"
            with self.subTest(mutation=mutation): self.assertTrue(compare_native_spans([source], [wrong]) or compare_source_fields(source, wrong))

    def test_exact_all_span_ids_parents_names_status_and_usage(self):
        expected = spans()
        self.assertEqual(compare_native_spans(expected, copy.deepcopy(expected)), [])
        for field, value in (("parent_span_id", "f" * 16), ("name", "other"), ("status", {"code": "ERROR"})):
            actual = copy.deepcopy(expected); actual[-1][field] = value
            with self.subTest(field=field): self.assertTrue(compare_native_spans(expected, actual))
        actual = copy.deepcopy(expected); actual[-1]["attributes"]["gen_ai.usage.total_tokens"] = 0
        self.assertTrue(compare_native_spans(expected, actual))

    def test_missing_extra_duplicate_or_empty_trace_fails(self):
        expected = spans()
        for actual in ([], expected[:-1], expected + [expected[-1]], expected + [{**expected[-1], "span_id": "6" * 16}]):
            self.assertTrue(compare_native_spans(expected, actual))

    def test_embedding_generation_and_tools_are_required(self):
        self.assertEqual(verify_inventory(spans()), [])
        for wanted in ("EMBEDDING", "GENERATION", "TOOL"):
            actual = [s for s in spans() if s["attributes"]["langfuse.observation.type"] != wanted]
            with self.subTest(type=wanted): self.assertTrue(verify_inventory(actual))

    def test_embedding_missing_usage_is_not_zero_or_chat_billing(self):
        result = usage_totals(spans())
        self.assertEqual(result["chat"], {"input": 13, "output": 5, "total": 18, "calls": 1})
        self.assertEqual(result["embedding"], {"input": None, "output": None, "total": None, "calls": 1})
        actual = spans(); actual[-1]["attributes"].update({"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0, "gen_ai.usage.total_tokens": 0})
        self.assertEqual(usage_totals(actual)["chat"]["total"], 0)
        actual[-1]["attributes"].pop("gen_ai.usage.total_tokens")
        self.assertEqual(usage_totals(actual)["chat"]["total"], 0)
        actual[-1]["attributes"].pop("gen_ai.usage.input_tokens")
        self.assertIsNone(usage_totals(actual)["chat"]["total"])

    def test_reported_fields_count_partial_calls_once_per_trace_and_span(self):
        source = spans()
        partial = copy.deepcopy(source[-1])
        partial.update(span_id="6" * 16)
        partial["attributes"] = {"langfuse.observation.type": "GENERATION", "gen_ai.usage.input_tokens": 0}
        another_trace = copy.deepcopy(source[-1])
        another_trace.update(trace_id="b" * 32)
        another_trace["attributes"].update({"gen_ai.usage.input_tokens": 2, "gen_ai.usage.output_tokens": 3, "gen_ai.usage.total_tokens": 5})
        # A chain rollup is not a call. Re-exporting the same call is not new usage.
        source[0]["attributes"].update({"gen_ai.usage.input_tokens": 999, "gen_ai.usage.output_tokens": 999, "gen_ai.usage.total_tokens": 1998})
        source.extend([partial, another_trace, copy.deepcopy(source[-1])])
        self.assertEqual(reported_usage(source), {"input": {"value": 15, "reportedCalls": 3}, "output": {"value": 8, "reportedCalls": 2}, "total": {"value": 23, "reportedCalls": 2}})
        self.assertEqual(usage_by_type(source), [
            {"type": "GENERATION", "callCount": 3, "usage": {"input": 15, "output": None, "total": None, "unknownReason": "usage_missing"}, "reportedUsage": {"input": {"value": 15, "reportedCalls": 3}, "output": {"value": 8, "reportedCalls": 2}, "total": {"value": 23, "reportedCalls": 2}}},
            {"type": "EMBEDDING", "callCount": 1, "usage": {"input": None, "output": None, "total": None, "unknownReason": "usage_missing"}, "reportedUsage": {"input": {"value": None, "reportedCalls": 0}, "output": {"value": None, "reportedCalls": 0}, "total": {"value": None, "reportedCalls": 0}}},
        ])

    def test_reported_zero_remains_measured_while_all_unknown_remains_null(self):
        for value in (0, None):
            source = spans()
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                if value is None: source[-1]["attributes"].pop("gen_ai.usage." + field)
                else: source[-1]["attributes"]["gen_ai.usage." + field] = 0
            with self.subTest(value=value):
                self.assertEqual(reported_usage(source), {field: {"value": value, "reportedCalls": 1 if value == 0 else 0} for field in ("input", "output", "total")})
                self.assertEqual(usage_by_type(source)[0]["usage"], {"input": value, "output": value, "total": value, "unknownReason": "usage_missing" if value is None else None})
                self.assertEqual(usage_by_type(source)[1]["reportedUsage"], {"input": {"value": None, "reportedCalls": 0}, "output": {"value": None, "reportedCalls": 0}, "total": {"value": None, "reportedCalls": 0}})
                self.assertEqual(canonical_usage(source), {"input": None, "output": None, "total": None})

    def test_type_aggregate_keeps_cross_type_billable_ancestry_through_tools(self):
        source = spans()
        source[1]["parent_span_id"] = source[-1]["span_id"]
        by_type = usage_by_type(source)
        self.assertEqual(by_type[0]["usage"], {"input": None, "output": None, "total": None, "unknownReason": "ambiguous_nested_generations"})
        self.assertEqual(by_type[0]["reportedUsage"], {"input": {"value": 13, "reportedCalls": 1}, "output": {"value": 5, "reportedCalls": 1}, "total": {"value": 18, "reportedCalls": 1}})
        self.assertEqual(by_type[1]["usage"], {"input": None, "output": None, "total": None, "unknownReason": "ambiguous_nested_generations"})
        # Equal span ids in a different trace do not form a billable ancestor.
        source[2]["trace_id"] = "b" * 32
        self.assertEqual(usage_by_type(source)[0]["usage"], {"input": 13, "output": 5, "total": 18, "unknownReason": None})

    def test_breakdown_lists_only_actual_call_types(self):
        source = spans()
        for kind, index in (("GENERATION", 4), ("EMBEDDING", 2)):
            with self.subTest(kind=kind):
                self.assertEqual([row["type"] for row in usage_by_type([source[index]])], [kind])
        self.assertEqual(usage_by_type(source[:2]), [])


def literal_report():
    result, evidence = business()
    raw = spans()
    for span in raw:
        span.update(start_time_unix_nano=100, end_time_unix_nano=200)
        span["attributes"]["user.id"] = "fake-rag-user"
    raw[0].update(start_time_unix_nano=0, end_time_unix_nano=1000)
    raw[0]["attributes"].update({"langfuse.observation.output": json.dumps(result)})
    raw[1]["attributes"].update({"langfuse.observation.input": json.dumps(evidence["tools"][0]["arguments"]), "langfuse.observation.output": json.dumps(evidence["tools"][0]["output"])})
    raw[3]["attributes"].update({"langfuse.observation.input": json.dumps(evidence["tools"][1]["arguments"]), "langfuse.observation.output": json.dumps(evidence["tools"][1]["output"])})
    raw[2]["attributes"].update({"langfuse.observation.input": json.dumps(["Orion"]), "langfuse.observation.output": json.dumps([[0.1, 0.2]])})
    raw[-1]["attributes"].update({"langfuse.observation.model.name": "fake", "langfuse.observation.output": json.dumps({"role": "assistant", "content": json.dumps(result), "tool_calls": []})})
    raw[-1]["attributes"]["langfuse.observation.input"] = json.dumps(evidence["messages"][:-1])
    fixture = [{"kind": "tool_result", "name": t["name"], "arguments": t["arguments"], "result": t["output"]} for t in evidence["tools"]]
    fixture += [{"kind": "embedding", "op": "query", "inputs": ["Orion"], "output": [[0.1, 0.2]], "dimensions": 2, "vector_count": 1},
                {"kind": "model_started", "call_id": "m1", "role": "worker", "model_name": "fake", "input_messages": evidence["messages"][:-1]},
                {"kind": "model_completed", "call_id": "m1", "role": "worker", "model_name": "fake", "content": json.dumps(result), "tool_calls": [], "usage": {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}}]
    report = {"schema_version": 1, "run_id": "literal", "framework": "langchain", "sdk": "lf4", "scenario": "stream", "status": "passed", "bounds": {"from_ms": 1, "to_ms": 2}, "sessions": [{"session_id": "s1", "tenant": "orion", "receipt": "receipt-391", "roots": [{"trace_id": "a" * 32, "phase": "initial", "status": "OK", "application_status": "completed", "output": result}], "business_results": [{"phase": "initial", "output": result, "messages": evidence["messages"]}], "fixture_expected": {"storage_gib": -999}, "fixture_ledger": fixture}]}
    exports = [{"http_status": 200, "otlp_response_decoded": True, "otlp_rejected_spans": 0, "spans": raw}]
    return report, exports


def checkpoint_report():
    report, exports = literal_report()
    report["scenario"] = "success"
    session, raw = report["sessions"][0], exports[0]["spans"]
    session["roots"][0]["phase"] = session["business_results"][0]["phase"] = "core"
    history = copy.deepcopy(session["business_results"][0]["messages"])
    history.append({"type": "human", "content": "Use the same retrieved receipt."})
    output = copy.deepcopy(session["roots"][0]["output"])
    root = copy.deepcopy(raw[0])
    root.update(trace_id="b" * 32, span_id="d" * 16, start_time_unix_nano=2000, end_time_unix_nano=3000)
    root["attributes"]["langfuse.observation.input"] = json.dumps({"request": history[-1]["content"]})
    model = copy.deepcopy(raw[4])
    model.update(trace_id="b" * 32, span_id="e" * 16, parent_span_id="d" * 16, start_time_unix_nano=2100, end_time_unix_nano=2900)
    model["attributes"]["langfuse.observation.input"] = json.dumps(history)
    raw.extend([root, model])
    session["roots"].append({"trace_id": "b" * 32, "phase": "followup", "status": "OK", "application_status": "completed", "input": {"request": history[-1]["content"]}, "output": output})
    session["business_results"].append({"phase": "followup", "output": output, "messages": history + [{"type": "ai", "content": json.dumps(output)}]})
    session["fixture_ledger"].extend([
        {"kind": "model_started", "call_id": "m2", "role": "worker", "model_name": "fake", "input_messages": history},
        {"kind": "model_completed", "call_id": "m2", "role": "worker", "model_name": "fake", "content": json.dumps(output), "tool_calls": [], "usage": {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}},
    ])
    return report, exports


def replace_final(report, exports, output, messages):
    session, raw = report["sessions"][0], exports[0]["spans"]
    session["business_results"][0].update(output=output, messages=messages)
    session["roots"][0]["output"] = output
    raw[0]["attributes"]["langfuse.observation.output"] = json.dumps(output)
    model = next(span for span in raw if span["span_id"] == "5" * 16)
    model["attributes"].update({"langfuse.observation.input": json.dumps(messages[:-1]),
                                "langfuse.observation.output": json.dumps({"role": "assistant", "content": json.dumps(output), "tool_calls": []})})
    for row in session["fixture_ledger"]:
        if row["kind"] == "model_started": row["input_messages"] = copy.deepcopy(messages[:-1])
        elif row["kind"] == "model_completed": row["content"] = json.dumps(output)


def controlled_report(scenario):
    report, exports = literal_report()
    report["scenario"] = scenario
    session, raw = report["sessions"][0], exports[0]["spans"]
    messages = copy.deepcopy(session["business_results"][0]["messages"][:3])
    error = "Controlled retrieval failure after actual vector search"
    if scenario == "no-results":
        retrieved = {"tenant": "orion", "documents": [], "receipt": "receipt-391"}
        tool = {"kind": "tool_result", "name": "retrieve_policy", "arguments": {"tenant": "orion"}, "result": retrieved}
        output = {"error": "no_results", "tenant": "orion", "receipt": "receipt-391"}
        messages[-1].update(content=json.dumps(retrieved), status="success")
        raw[1]["attributes"]["langfuse.observation.output"] = json.dumps(retrieved)
    else:
        tool = {"kind": "tool_error", "name": "retrieve_policy", "arguments": {"tenant": "orion"}, "error": error}
        output = {"error": "retrieval_error", "tenant": "orion", "message": error}
        messages[-1].update(content=error, status="error")
        raw[1].update(status={"code": "ERROR"})
        raw[1]["attributes"]["langfuse.observation.output"] = error
    raw[:] = [span for span in raw if span["span_id"] != "4" * 16]
    remaining = [row for row in session["fixture_ledger"] if row["kind"] not in ("tool_result", "tool_error")]
    session["fixture_ledger"] = [{"kind": "retrieval", "attempt": 1,
        "query": "Storage policy for tenant orion. Retention: 14 days. Daily storage: 10 GiB.",
        "tenant": "orion", "document_ids": [] if scenario == "no-results" else ["policy-orion"]}, tool, *remaining]
    messages.append({"type": "ai", "content": json.dumps(output)})
    replace_final(report, exports, output, messages)
    return report, exports


def retry_report():
    report, exports = literal_report()
    report["scenario"] = "retry"
    session, raw = report["sessions"][0], exports[0]["spans"]
    error = "Controlled retrieval failure after actual vector search"
    failed = copy.deepcopy(raw[1])
    failed.update(span_id="6" * 16, start_time_unix_nano=100, end_time_unix_nano=120, status={"code": "ERROR"})
    failed["attributes"]["langfuse.observation.output"] = error
    raw.append(failed)
    raw[1].update(start_time_unix_nano=130, end_time_unix_nano=160)
    raw[2].update(start_time_unix_nano=140, end_time_unix_nano=150)
    raw[3].update(start_time_unix_nano=180, end_time_unix_nano=190)
    raw[4].update(start_time_unix_nano=210, end_time_unix_nano=240)
    row = {"kind": "retrieval", "query": "Storage policy for tenant orion. Retention: 14 days. Daily storage: 10 GiB.", "tenant": "orion", "document_ids": ["policy-orion"]}
    session["fixture_ledger"][:0] = [{**row, "attempt": 1},
        {"kind": "tool_error", "name": "retrieve_policy", "arguments": {"tenant": "orion"}, "error": error},
        {**row, "attempt": 2}]
    messages = copy.deepcopy(session["business_results"][0]["messages"])
    messages[1:1] = [{"type": "ai", "content": "", "tool_calls": [{"name": "retrieve_policy", "id": "r0", "args": {"tenant": "orion"}}]},
        {"type": "tool", "name": "retrieve_policy", "tool_call_id": "r0", "content": error, "status": "error"}]
    replace_final(report, exports, session["roots"][0]["output"], messages)
    return report, exports


def relay_cancel_report():
    """Literal 4/4 generations, with only cancelled SDK cleanup past task end."""
    report, exports = literal_report()
    report.update(framework="deepagents", sdk="nemo-relay", scenario="cancel",
                  versions={"nemo-relay": "0.9.3"}, known_limits=[{"id": "NR-002", "description": "obsolete closure unsupported"}])
    session, raw = report["sessions"][0], exports[0]["spans"]
    session["business_results"] = []
    session["roots"][0].update(status="ERROR", application_status="cancelled")
    raw[0].update(name="invoke_agent application-request", status={"code": "ERROR"})
    for span in (raw[1], raw[3], raw[4]):
        span["parent_span_id"] = "8" * 16
    raw[1].update(start_time_unix_nano=200, end_time_unix_nano=250)
    raw[2].update(start_time_unix_nano=210, end_time_unix_nano=240)
    raw[3].update(start_time_unix_nano=400, end_time_unix_nano=450)
    raw[4].update(start_time_unix_nano=120, end_time_unix_nano=300)
    task_args = {"subagent_type": "researcher", "description": "Retrieve and calculate Orion policy"}
    raw.extend([
        {"trace_id": "a" * 32, "span_id": "8" * 16, "parent_span_id": "9" * 16, "name": "invoke_agent researcher", "status": {"code": "ERROR"}, "attributes": {"gen_ai.operation.name": "invoke_agent"}, "start_time_unix_nano": 110, "end_time_unix_nano": 650},
        {"trace_id": "a" * 32, "span_id": "6" * 16, "parent_span_id": "9" * 16, "name": "task", "status": {"code": "ERROR"}, "attributes": {"langfuse.observation.type": "TOOL", "langfuse.observation.input": json.dumps(task_args)}, "start_time_unix_nano": 100, "end_time_unix_nano": 600},
        {"trace_id": "a" * 32, "span_id": "9" * 16, "parent_span_id": "1" * 16, "name": "invoke_agent rag-orchestrator", "status": {"code": "ERROR"}, "attributes": {"gen_ai.operation.name": "invoke_agent"}, "start_time_unix_nano": 10, "end_time_unix_nano": 800},
    ])
    fixture = session["fixture_ledger"]
    parent_call = {"name": "task", "id": "task-literal", "args": task_args}
    parent = copy.deepcopy(raw[4])
    parent.update(span_id="7" * 16, parent_span_id="9" * 16, start_time_unix_nano=20, end_time_unix_nano=80)
    parent["attributes"].update({"langfuse.observation.input": "[]", "langfuse.observation.output": json.dumps({"content": "", "tool_calls": [parent_call]})})
    second = copy.deepcopy(raw[4])
    second.update(span_id="b" * 16, start_time_unix_nano=310, end_time_unix_nano=390)
    cancel = copy.deepcopy(raw[4])
    cancel.update(span_id="c" * 16, start_time_unix_nano=500, end_time_unix_nano=610, status={"code": "ERROR"})
    for key in [key for key in cancel["attributes"] if key.startswith("gen_ai.usage.") or key == "langfuse.observation.output"]:
        cancel["attributes"].pop(key)
    raw.extend([parent, second, cancel])
    fixture.extend([
        {"kind": "model_started", "call_id": "m2", "role": "parent", "model_name": "fake", "input_messages": []},
        {"kind": "model_completed", "call_id": "m2", "role": "parent", "model_name": "fake", "content": "", "tool_calls": [parent_call], "usage": {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}},
        {**copy.deepcopy(fixture[-2]), "call_id": "m3"},
        {**copy.deepcopy(fixture[-1]), "call_id": "m3"},
        {**copy.deepcopy(fixture[-2]), "call_id": "m4"},
        {"kind": "model_partial", "call_id": "m4", "role": "worker", "text": "partial"},
        {"kind": "model_cancelled", "call_id": "m4", "role": "worker"},
    ])
    return report, exports


class SourceReportTests(unittest.TestCase):
    def test_new_python_metadata_and_transport_contract_rejects_corruption(self):
        report, exports = literal_report()
        report.update(sdk="langfuse", metadata_contract="python-sdk-json-types-v1", versions={"langfuse": "5.0.2"})
        exports[0].update(sdk_version="5.0.2", content_type="application/x-protobuf",
                          content_encoding="gzip", ingestion_version="4")
        root = exports[0]["spans"][0]
        root.update(scope_name="langfuse-sdk", scope_version="5.0.2", resource={"telemetry.sdk.language": "python"})
        literal = {"text": "中文 🧪", "numeric_string": "42", "boolean_string": "true", "null_string": "null",
                   "quoted_string": '"quoted"', "empty_string": "", "number": 42, "boolean": True,
                   "object": {"nested": None, "value": "42"}, "array": ["x", 2, False, None]}
        root["attributes"].update({"langfuse.observation.metadata." + key: json.dumps(value) for key, value in literal.items()})
        self.assertTrue(verify_source_report(report, exports)["source_verified"])
        for mutation in ("numeric_string", "nested_boolean", "missing", "gzip", "protobuf", "version", "ingestion"):
            changed = copy.deepcopy(exports)
            attrs = changed[0]["spans"][0]["attributes"]
            if mutation == "numeric_string": attrs["langfuse.observation.metadata.numeric_string"] = "42"
            elif mutation == "nested_boolean": attrs["langfuse.observation.metadata.array"] = '["x",2,0,null]'
            elif mutation == "missing": attrs.pop("langfuse.observation.metadata.empty_string")
            elif mutation == "gzip": changed[0]["content_encoding"] = "identity"
            elif mutation == "protobuf": changed[0]["content_type"] = "application/json"
            elif mutation == "version": changed[0]["sdk_version"] = "4.16.0"
            else: changed[0]["ingestion_version"] = "3"
            with self.subTest(mutation=mutation):
                self.assertFalse(verify_source_report(report, changed)["source_verified"])

    def test_business_root_user_is_an_independent_literal(self):
        for mutation in ("missing", "wrong"):
            report, exports = literal_report()
            if mutation == "missing": exports[0]["spans"][0]["attributes"].pop("user.id")
            else: exports[0]["spans"][0]["attributes"]["user.id"] = "invented"
            with self.subTest(mutation=mutation): self.assertFalse(verify_source_report(report, exports)["source_verified"])

    def test_handled_completed_turns_require_nonempty_exact_results(self):
        for scenario, code in (("no-results", "no_results"), ("retrieval-error", "retrieval_error")):
            report, exports = controlled_report(scenario)
            self.assertTrue(verify_source_report(report, exports)["passed"])
            for mutation in ("delete", "empty", "wrong_error", "wrong_phase", "duplicate"):
                wrong_report = copy.deepcopy(report)
                results = wrong_report["sessions"][0]["business_results"]
                if mutation == "delete": results.clear()
                elif mutation == "empty": results[0]["output"] = {}
                elif mutation == "wrong_error": results[0]["output"] = {"error": "invented"}
                elif mutation == "wrong_phase": results[0]["phase"] = "invented"
                else: results.append(copy.deepcopy(results[0]))
                with self.subTest(scenario=scenario, mutation=mutation):
                    wrong = verify_source_report(wrong_report, exports)
                    self.assertFalse(wrong["passed"])
                    guard = "business_result_present" if mutation == "delete" else "completed_turn_result_output" if mutation == "empty" else "controlled_error.0" if mutation == "wrong_error" else "completed_turn_result_inventory"
                    self.assertFalse(wrong["checks"]["session.0." + guard])

    def test_successful_retrieval_and_calculation_cannot_be_relabelled_as_a_controlled_error(self):
        for scenario, code in (("no-results", "no_results"), ("retrieval-error", "retrieval_error")):
            report, exports = literal_report()
            report["scenario"] = scenario
            messages = copy.deepcopy(report["sessions"][0]["business_results"][0]["messages"])
            output = {"error": code, "tenant": "orion", "receipt": "receipt-391"}
            messages[-1]["content"] = json.dumps(output)
            replace_final(report, exports, output, messages)
            with self.subTest(scenario=scenario):
                wrong = verify_source_report(report, exports)
                self.assertFalse(wrong["source_verified"])
                self.assertFalse(wrong["checks"].get("session.0.controlled_retrieval_path"))

    def test_controlled_errors_require_actual_retrieval_empty_or_error_and_observed_toolmessage(self):
        for scenario in ("no-results", "retrieval-error"):
            report, exports = controlled_report(scenario)
            self.assertTrue(verify_source_report(report, exports)["passed"])
            for mutation in ("missing_retrieval", "invented_ids", "toolmessage", "tool_call", "missing_tool_call_id", "wrong_sdk_output", "calculator"):
                wrong_report, wrong_exports = copy.deepcopy(report), copy.deepcopy(exports)
                session, raw = wrong_report["sessions"][0], wrong_exports[0]["spans"]
                if mutation == "missing_retrieval": session["fixture_ledger"].pop(0)
                elif mutation == "invented_ids": session["fixture_ledger"][0]["document_ids"] = ["invented"]
                elif mutation == "toolmessage":
                    for messages in [session["business_results"][0]["messages"], *[row["input_messages"] for row in session["fixture_ledger"] if row["kind"] == "model_started"]]:
                        messages[:] = [message for message in messages if message.get("type") != "tool"]
                    raw[-1]["attributes"]["langfuse.observation.input"] = json.dumps(session["business_results"][0]["messages"][:-1])
                elif mutation in ("tool_call", "missing_tool_call_id"):
                    for messages in [session["business_results"][0]["messages"], *[row["input_messages"] for row in session["fixture_ledger"] if row["kind"] == "model_started"]]:
                        if mutation == "tool_call": messages[1]["tool_calls"][0]["id"] = "unrelated"
                        else:
                            messages[1]["tool_calls"][0].pop("id")
                            messages[2].pop("tool_call_id")
                    raw[-1]["attributes"]["langfuse.observation.input"] = json.dumps(session["business_results"][0]["messages"][:-1])
                elif mutation == "wrong_sdk_output": raw[1]["attributes"]["langfuse.observation.output"] = "Invented unrelated error"
                else: session["fixture_ledger"].append({"kind": "tool_error", "name": "calculate_storage", "error": "not allowed", "arguments": {}})
                with self.subTest(scenario=scenario, mutation=mutation):
                    wrong = verify_source_report(wrong_report, wrong_exports)
                    self.assertFalse(wrong["source_verified"])
                    self.assertFalse(wrong["checks"].get("session.0.controlled_retrieval_path"))

    def test_retry_requires_error_then_successful_retrieval_then_calculation_in_ledger_and_sdk_time(self):
        report, exports = retry_report()
        self.assertTrue(verify_source_report(report, exports)["passed"])
        for mutation in ("ledger_error_after_success", "sdk_error_after_success", "sdk_calculation_before_retrieval", "missing_attempt", "wrong_error", "toolmessage"):
            wrong_report, wrong_exports = copy.deepcopy(report), copy.deepcopy(exports)
            session, raw = wrong_report["sessions"][0], wrong_exports[0]["spans"]
            if mutation == "ledger_error_after_success": session["fixture_ledger"].append(session["fixture_ledger"].pop(1))
            elif mutation == "sdk_error_after_success": raw[-1].update(start_time_unix_nano=250, end_time_unix_nano=260)
            elif mutation == "sdk_calculation_before_retrieval": raw[3].update(start_time_unix_nano=121, end_time_unix_nano=129)
            elif mutation == "missing_attempt": session["fixture_ledger"].pop(2)
            elif mutation == "wrong_error": session["fixture_ledger"][1]["error"] = "Invented unrelated error"
            else:
                for messages in [session["business_results"][0]["messages"], *[row["input_messages"] for row in session["fixture_ledger"] if row["kind"] == "model_started"]]:
                    messages.pop(2)
                raw[4]["attributes"]["langfuse.observation.input"] = json.dumps(session["business_results"][0]["messages"][:-1])
            with self.subTest(mutation=mutation):
                wrong = verify_source_report(wrong_report, wrong_exports)
                self.assertFalse(wrong["source_verified"])
                self.assertFalse(wrong["checks"]["session.0.retry_then_success"])

    def test_checkpoint_requires_both_turns_prior_messages_receipt_and_no_repeated_work(self):
        report, exports = checkpoint_report()
        self.assertTrue(verify_source_report(report, exports)["passed"], verify_source_report(report, exports)["failures"])
        for mutation in ("whole_followup", "result_only", "history", "receipt", "tool", "embedding"):
            wrong_report, wrong_exports = copy.deepcopy(report), copy.deepcopy(exports)
            session, raw = wrong_report["sessions"][0], wrong_exports[0]["spans"]
            if mutation == "whole_followup":
                session["roots"].pop(); session["business_results"].pop()
                session["fixture_ledger"] = [r for r in session["fixture_ledger"] if r.get("call_id") != "m2"]
                raw[:] = [r for r in raw if r["trace_id"] != "b" * 32]
            elif mutation == "result_only": session["business_results"].pop()
            elif mutation == "history":
                short = [{"type": "human", "content": "Use the same retrieved receipt."}]
                raw[-1]["attributes"]["langfuse.observation.input"] = json.dumps(short)
                session["fixture_ledger"][-2]["input_messages"] = short
            elif mutation == "receipt": session["business_results"][-1]["output"]["receipt"] = "invented"
            else:
                span = copy.deepcopy(raw[1 if mutation == "tool" else 2])
                span.update(trace_id="b" * 32, span_id="f" * 16, parent_span_id="d" * 16)
                raw.append(span)
            with self.subTest(mutation=mutation):
                wrong = verify_source_report(wrong_report, wrong_exports)
                self.assertFalse(wrong["passed"])
                guard = {"whole_followup": "checkpoint_root_inventory", "result_only": "completed_turn_result_inventory", "history": "checkpoint_prior_messages", "receipt": "checkpoint_same_retrieval_receipt", "tool": "checkpoint_no_repeated_work", "embedding": "checkpoint_no_repeated_work"}[mutation]
                self.assertFalse(wrong["checks"]["session.0." + guard])

    def test_relay_cancel_sibling_cleanup_tail_is_explicit_and_bounded_by_orchestrator_root(self):
        report, exports = relay_cancel_report()
        result = verify_source_report(report, exports)
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["generation_accounting"][0], {"session_index": 0, "started": 4, "completed": 3, "cancelled": 1, "exported": 4, "exported_cancelled": 1, "missing_cancelled": 0})
        tail = result["cancel_cleanup_tails"][0]
        self.assertEqual(tail["researcher_tail_ns"], 50)
        self.assertEqual(tail["cancelled_generation_tails"][0]["tail_ns"], 10)
        self.assertTrue(tail["orchestrator_root_contained"])
        self.assertTrue(tail["accepted"])
        self.assertNotIn("unsupported", result["known_limits"][0]["description"])
        cancellation_record = copy.deepcopy(report)
        cancellation_record["sessions"][0]["business_results"] = [{"phase": "initial", "error": "CancelledError", "output": None}]
        self.assertTrue(verify_source_report(cancellation_record, exports)["passed"])
        for mutation in ("sdk", "version", "scenario", "limit", "task_status", "worker_status", "orchestrator_status", "task_parent", "worker_missing", "task_missing", "generation_missing", "cancel_status", "cancel_input", "cancel_identity", "cancel_usage", "worker_start", "cancel_start", "completed_start", "embedding_start", "tool_start", "completed_tail", "embedding_tail", "tool_tail", "root_tail", "orchestrator_tail", "orchestrator_after_root"):
            wrong_report, wrong_exports = copy.deepcopy(report), copy.deepcopy(exports)
            raw = wrong_exports[0]["spans"]
            if mutation == "sdk":
                wrong_report["sdk"] = "langfuse"
                raw[-1]["attributes"]["langfuse.observation.output"] = json.dumps({"partial_text": "partial", "complete": False})
            elif mutation == "version": wrong_report["versions"]["nemo-relay"] = "0.9.4"
            elif mutation == "scenario": wrong_report["scenario"] = "cancellation"
            elif mutation == "limit": wrong_report["known_limits"] = []
            elif mutation == "task_status": raw[6]["status"] = {"code": "OK"}
            elif mutation == "worker_status": raw[5]["status"] = {"code": "OK"}
            elif mutation == "orchestrator_status": raw[7]["status"] = {"code": "OK"}
            elif mutation == "task_parent": raw[6]["parent_span_id"] = "1" * 16
            elif mutation == "worker_missing": raw.pop(5)
            elif mutation == "task_missing": raw.pop(6)
            elif mutation == "generation_missing": raw.pop()
            elif mutation == "cancel_status": raw[-1]["status"] = {"code": "OK"}
            elif mutation == "cancel_input": raw[-1]["attributes"].pop("langfuse.observation.input")
            elif mutation == "cancel_identity": raw[-1]["attributes"]["langfuse.observation.model.name"] = "invented"
            elif mutation == "cancel_usage": raw[-1]["attributes"]["gen_ai.usage.input_tokens"] = 0
            elif mutation == "worker_start": raw[5]["start_time_unix_nano"] = 99
            elif mutation == "cancel_start": raw[-1]["start_time_unix_nano"] = 99
            elif mutation == "completed_start": raw[4]["start_time_unix_nano"] = 99
            elif mutation == "embedding_start": raw[2]["start_time_unix_nano"] = 99
            elif mutation == "tool_start": raw[3]["start_time_unix_nano"] = 99
            elif mutation == "completed_tail": raw[4]["end_time_unix_nano"] = 601
            elif mutation == "embedding_tail": raw[2]["end_time_unix_nano"] = 601
            elif mutation == "tool_tail": raw[3]["end_time_unix_nano"] = 601
            elif mutation == "root_tail": raw[0]["end_time_unix_nano"] = 649
            elif mutation == "orchestrator_tail": raw[7]["end_time_unix_nano"] = 649
            else: raw[7]["end_time_unix_nano"] = 1001
            with self.subTest(mutation=mutation):
                wrong = verify_source_report(wrong_report, wrong_exports)
                self.assertFalse(wrong["passed"])
                if mutation in ("sdk", "version", "scenario", "limit", "task_status", "worker_status", "orchestrator_status", "task_parent", "worker_start", "cancel_start", "completed_start", "embedding_start", "tool_start", "completed_tail", "embedding_tail", "tool_tail", "root_tail", "orchestrator_tail", "orchestrator_after_root"):
                    self.assertFalse(wrong["checks"]["session.0.subagent_worker_task_scope"])

    def test_application_success_cannot_be_error_even_if_sdk_snapshot_is_also_changed(self):
        report, exports = literal_report()
        report["sessions"][0]["roots"][0]["status"] = "ERROR"
        exports[0]["spans"][0]["status"] = {"code": "ERROR"}
        self.assertFalse(verify_source_report(report, exports)["passed"])
    def test_deepagents_researcher_scope_task_and_parent_worker_are_real(self):
        report, exports = literal_report()
        report["framework"] = "deepagents"
        raw = exports[0]["spans"]
        raw[1]["parent_span_id"] = "8" * 16
        raw[3]["parent_span_id"] = "8" * 16
        raw[-1]["parent_span_id"] = "8" * 16
        raw.append({"trace_id": "a" * 32, "span_id": "8" * 16, "parent_span_id": "1" * 16, "name": "invoke_agent researcher", "status": {"code": "OK"}, "attributes": {"gen_ai.operation.name": "invoke_agent"}, "start_time_unix_nano": 100, "end_time_unix_nano": 200})
        task_args = {"subagent_type": "researcher", "description": "Retrieve and calculate Orion policy"}
        task_output = report["sessions"][0]["business_results"][0]["output"]
        raw.append({"trace_id": "a" * 32, "span_id": "6" * 16, "parent_span_id": "1" * 16, "name": "task", "status": {"code": "OK"}, "attributes": {"langfuse.observation.type": "TOOL", "langfuse.observation.input": json.dumps(task_args), "langfuse.observation.output": json.dumps(task_output)}, "start_time_unix_nano": 90, "end_time_unix_nano": 210})
        parent = copy.deepcopy(raw[4]); parent.update(span_id="7" * 16, parent_span_id="1" * 16)
        raw.append(parent)
        session = report["sessions"][0]
        fixture = session["fixture_ledger"]
        fixture.append({**fixture[-1], "call_id": "m2", "role": "parent"})
        fixture[:] = [row for row in fixture if row["kind"] != "model_started"]
        history = session["business_results"][0]["messages"][:-1]
        for message in history:
            if message["type"] == "tool": message.pop("name", None)
        fixture.extend([{"kind": "model_started", "call_id": "m1", "role": "worker", "model_name": "fake", "input_messages": history}, {"kind": "model_started", "call_id": "m2", "role": "parent", "model_name": "fake", "input_messages": []}])
        raw[4]["attributes"]["langfuse.observation.input"] = json.dumps(history)
        raw[7]["attributes"]["langfuse.observation.input"] = "[]"
        session["business_results"][0]["messages"] = [{"type": "ai", "content": "", "tool_calls": [{"name": "task", "id": "task-literal", "args": task_args}]}, {"type": "tool", "tool_call_id": "task-literal", "content": json.dumps(task_output)}, {"type": "ai", "content": json.dumps(session["business_results"][0]["output"])}]
        self.assertTrue(verify_source_report(report, exports)["passed"], verify_source_report(report, exports)["failures"])
        for mutation in ("worker_parent", "task_parent", "task_interval", "task_missing", "task_input", "task_output", "child_toolmessage"):
            wrong_report, wrong_exports = copy.deepcopy(report), copy.deepcopy(exports)
            if mutation == "worker_parent": wrong_exports[0]["spans"][4]["parent_span_id"] = "1" * 16
            elif mutation == "task_parent": wrong_exports[0]["spans"][6]["parent_span_id"] = "8" * 16
            elif mutation == "task_interval": wrong_exports[0]["spans"][6]["end_time_unix_nano"] = 199
            elif mutation == "task_missing": wrong_exports[0]["spans"].pop(6)
            elif mutation == "task_input": wrong_exports[0]["spans"][6]["attributes"]["langfuse.observation.input"] = "{}"
            elif mutation == "task_output": wrong_exports[0]["spans"][6]["attributes"]["langfuse.observation.output"] = "{}"
            else:
                for row in wrong_report["sessions"][0]["fixture_ledger"]:
                    if row["kind"] == "model_started": row["input_messages"] = [m for m in row.get("input_messages", []) if m["type"] != "tool"]
            with self.subTest(mutation=mutation): self.assertFalse(verify_source_report(wrong_report, wrong_exports)["passed"])

    def test_model_outputs_identity_and_nonbillable_wrappers(self):
        for mutation in ("model_content", "model_identity", "model_missing_input", "model_missing_output", "model_extra", "model_completed_usage"):
            report, exports = literal_report()
            generation = exports[0]["spans"][-1]
            if mutation == "model_content": generation["attributes"]["langfuse.observation.output"] = json.dumps({"role": "assistant", "content": "invented", "tool_calls": []})
            elif mutation == "model_identity": generation["attributes"]["langfuse.observation.model.name"] = "different"
            elif mutation == "model_missing_output": generation["attributes"].pop("langfuse.observation.output")
            elif mutation == "model_missing_input": generation["attributes"].pop("langfuse.observation.input")
            elif mutation == "model_extra": exports[0]["spans"].append({**generation, "span_id": "7" * 16})
            else: report["sessions"][0]["fixture_ledger"][-1]["usage"]["total_tokens"] = 0
            with self.subTest(mutation=mutation): self.assertFalse(verify_source_report(report, exports)["passed"])
        report, exports = literal_report()
        wrapper = copy.deepcopy(exports[0]["spans"][-1])
        wrapper.update(span_id="7" * 16, name="model-wrapper")
        wrapper["attributes"]["langfuse.observation.type"] = "CHAIN"
        exports[0]["spans"].append(wrapper)
        self.assertTrue(verify_source_report(report, exports)["passed"])

    def test_reported_zero_unknown_and_known_usage_remain_distinct(self):
        for scenario, usage in (("zero-usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}), ("unknown-usage", None)):
            report, exports = literal_report()
            report["scenario"] = scenario
            report["sessions"][0]["fixture_ledger"][-1]["usage"] = usage
            attrs = exports[0]["spans"][-1]["attributes"]
            for key in [k for k in attrs if k.startswith("gen_ai.usage.")]: attrs.pop(key)
            if usage is not None:
                attrs.update({"gen_ai.usage." + k: v for k, v in usage.items()})
            self.assertTrue(verify_source_report(report, exports)["passed"])
            attrs["gen_ai.usage.input_tokens"] = 13
            self.assertFalse(verify_source_report(report, exports)["passed"])

    def test_real_source_ledger_and_sdk_capture_pass_without_fixture_expected(self):
        report, exports = literal_report()
        result = verify_source_report(report, exports)
        self.assertTrue(result["passed"], result["failures"])
        self.assertFalse(result["platform_query_verified"])

    def test_missing_tool_embedding_generation_parent_and_usage_mutations_fail(self):
        for mutation in ("tool", "embedding", "generation", "parent", "usage", "receipt", "toolmessage", "ingest_rejection", "missing_root", "root_output", "extra_tool", "empty_exports"):
            report, exports = literal_report()
            raw = exports[0]["spans"]
            if mutation in ("tool", "embedding", "generation"):
                wanted = {"tool": "TOOL", "embedding": "EMBEDDING", "generation": "GENERATION"}[mutation]
                exports[0]["spans"] = [s for s in raw if s["attributes"]["langfuse.observation.type"] != wanted]
            elif mutation == "parent": raw[2]["parent_span_id"] = "f" * 16
            elif mutation == "usage": raw[-1]["attributes"]["gen_ai.usage.total_tokens"] = 0
            elif mutation == "receipt": report["sessions"][0]["business_results"][0]["output"]["receipt"] = "invented"
            elif mutation == "toolmessage": report["sessions"][0]["business_results"][0]["messages"].pop(2)
            elif mutation == "ingest_rejection": exports[0]["otlp_rejected_spans"] = 1
            elif mutation == "missing_root": report["sessions"][0]["roots"][0]["trace_id"] = "b" * 32
            elif mutation == "root_output": raw[0]["attributes"]["langfuse.observation.output"] = "{}"
            elif mutation == "extra_tool": raw.append({**copy.deepcopy(raw[1]), "span_id": "9" * 16})
            else: exports = []
            with self.subTest(mutation=mutation): self.assertFalse(verify_source_report(report, exports)["passed"])

    def test_cancelled_root_must_be_error_and_unknown_cannot_become_zero(self):
        report, exports = literal_report()
        report["scenario"] = "cancellation"
        report["sessions"][0]["business_results"] = []
        report["sessions"][0]["fixture_ledger"] = [r for r in report["sessions"][0]["fixture_ledger"] if r["kind"] != "model_completed"] + [{"kind": "model_partial", "call_id": "m1", "role": "worker", "text": "partial"}, {"kind": "model_cancelled", "call_id": "m1", "role": "worker"}]
        report["sessions"][0]["roots"][0]["status"] = "ERROR"
        report["sessions"][0]["roots"][0]["application_status"] = "cancelled"
        exports[0]["spans"][0]["status"] = {"code": "ERROR"}
        exports[0]["spans"][-1]["attributes"] = {"langfuse.observation.type": "GENERATION", "langfuse.observation.model.name": "fake", "langfuse.observation.input": exports[0]["spans"][-1]["attributes"]["langfuse.observation.input"], "langfuse.observation.output": json.dumps({"partial_text": "partial", "complete": False})}
        exports[0]["spans"][-1]["status"] = {"code": "ERROR"}
        self.assertTrue(verify_source_report(report, exports)["passed"])
        for mutation in ("generation_missing", "generation_ok", "generation_input", "generation_identity", "generation_output", "partial_text", "cancel_usage"):
            wrong_report, wrong_exports = copy.deepcopy(report), copy.deepcopy(exports)
            generation = wrong_exports[0]["spans"][-1]
            if mutation == "generation_missing":
                wrong_exports[0]["spans"].pop()
                wrong_report.update(sdk="nemo-relay", known_limits=[{"id": "NR-002"}])
            elif mutation == "generation_ok": generation["status"] = {"code": "OK"}
            elif mutation == "generation_input": generation["attributes"].pop("langfuse.observation.input")
            elif mutation == "generation_identity": generation["attributes"]["langfuse.observation.model.name"] = "different"
            elif mutation == "generation_output": generation["attributes"].pop("langfuse.observation.output")
            elif mutation == "partial_text": generation["attributes"]["langfuse.observation.output"] = json.dumps({"partial_text": "fabricated", "complete": False})
            else: generation["attributes"]["gen_ai.usage.input_tokens"] = 0
            with self.subTest(mutation=mutation): self.assertFalse(verify_source_report(wrong_report, wrong_exports)["passed"])
        exports[0]["spans"][0]["status"] = {"code": "OK"}
        self.assertFalse(verify_source_report(report, exports)["passed"])


class PlatformContractTests(unittest.TestCase):
    def platform_input(self, mutation=None, *, chat_values=(13, 5, 18), nested_billable=False,
                       root_status=None, child_status=None, trace_errors=0):
        report, exports = literal_report()
        for span in exports[0]["spans"]:
            span.update(start_time_unix_nano=1000000000, end_time_unix_nano=1002000000)
        target = {"endpoint": "http://fake-platform.invalid", "space_id": "space-literal", "agent_uuid": "agent-literal", "agent_product": "framework", "space_key": "test-only-not-a-secret", "authenticated_user_id": "authenticated-framework-user"}
        source = exports[0]["spans"]
        for span, code in ((source[0], root_status), (source[1], child_status)):
            if code is not None:
                span["status"] = {"code": code}
                span["attributes"]["langfuse.observation.level"] = "ERROR" if code == "ERROR" else "DEFAULT"
        for field, value in zip(("input_tokens", "output_tokens", "total_tokens"), chat_values):
            if value is None: source[-1]["attributes"].pop("gen_ai.usage." + field)
            else: source[-1]["attributes"]["gen_ai.usage." + field] = value
        if nested_billable:
            source[1]["parent_span_id"] = source[-1]["span_id"]
        for span in source:
            span["attributes"].update({"session.id": "s1", "gen_ai.user.id": "fake-rag-user", "langfuse.user.id": "fake-rag-user"})
        source[0]["attributes"].update({"langfuse.trace.name": "literal trace", "langfuse.trace.metadata": '{"corpus":"synthetic"}', "langfuse.trace.tags": '["rag"]', "langfuse.release": "literal-release", "langfuse.version": "literal-version", "langfuse.environment": "test"})
        actual = copy.deepcopy(source)
        for span in actual:
            span["attributes"].pop("user.id", None)
            span["resource"] = {"aivision.space.id": target["space_id"], "aivision.agent.id": target["agent_uuid"], "aivision.agent.product": target["agent_product"], "aivision.user.id": target["authenticated_user_id"]}
        if mutation == "missing_span": actual.pop()
        if mutation == "parent": actual[-1]["parent_span_id"] = "f" * 16
        if mutation == "timestamp": actual[-1]["start_time_unix_nano"] += 1
        if mutation == "usage": actual[-1]["attributes"]["gen_ai.usage.total_tokens"] = 0
        if mutation == "source_io": actual[1]["attributes"].pop("langfuse.observation.output")
        if mutation == "auth_identity": actual[-1]["resource"]["aivision.agent.id"] = "other-agent"
        def query(identity, path, params=None, body=None, key=None, basic=False):
            params = params or {}
            if path.endswith("/detail"):
                if key: return (200, {"code": 0, "data": {"spans": actual}}) if mutation == "invalid_auth_leak" else (401, {})
                if "/spaces/space-literal/" not in path: return 403, {}
                if params.get("agent_uuid") != "agent-literal": return 400, {}
                if params.get("from", 0) > 2: return 404, {}
                return 200, {"code": 0, "data": {"spans": actual}}
            if path.startswith("/api/public/traces/"):
                trace = trace_fields()
                trace.update(htmlPath="/project/agent-literal/traces/" + "a" * 32, latency=0.002, totalCost=None, scores=[], observations=v1_rows())
                if mutation and mutation.startswith("trace_"):
                    field = mutation[len("trace_"):]
                    trace[field] = {"wrong": True} if field in ("input", "output", "metadata") else [] if field == "observations" else "invented"
                return 200, trace
            if path == "/api/public/sessions/s1":
                trace = trace_fields()
                if mutation == "session_user": trace["userId"] = "invented"
                if mutation == "session_v1_inventory": trace["id"] = "b" * 32
                return 200, {"id": "s1", "projectId": "agent-literal", "createdAt": "1970-01-01T00:00:01.000000000Z", "environment": "test", "traces": [trace]}
            if path == "/api/public/observations":
                rows = v1_rows()
                if mutation == "v1_type": rows[-1]["type"] = "EMBEDDING"
                if mutation in ("v1_input", "v1_output", "v1_model", "v1_level", "v1_time"):
                    rows[-1].pop({"v1_input": "input", "v1_output": "output", "v1_model": "model", "v1_level": "level", "v1_time": "startTime"}[mutation])
                if mutation and mutation.startswith("legacy_"):
                    rows[-1 if "known" in mutation else 2]["usage"][mutation.split("_")[-1]] = 99999
                if mutation and mutation.startswith("legacyalias_"): rows[-1][mutation.split("_")[-1]] = 99999
                page = params["page"]
                return 200, {"data": rows[(page-1)*2:page*2], "meta": {"totalItems": 5, "totalPages": 3}}
            if path.endswith("/sessions/usage"):
                return 200, {"code": 0, "data": {"data": [{"id": "s1", "usage": canonical_usage(source)}]}}
            if path.endswith("/sessions/io"):
                return 200, {"code": 0, "data": {"data": [{"id": "s1", "inputValue": None, "outputValue": report["sessions"][0]["roots"][0]["output"], "inputSource": {"traceId": "a" * 32}, "outputSource": {"traceId": "b" * 32 if mutation == "session_scope" else "a" * 32}}]}}
            if path.endswith("/traces/metrics"):
                chat_usage = dict(zip(("input", "output", "total"), chat_values))
                groups = ([{"dimension": "model", "value": "fake", "callCount": 1, "usage": dict(chat_usage)}, {"dimension": "model", "value": None, "callCount": 1, "usage": {"input": None, "output": None, "total": None}}] if body["groupBy"] == "model" else [{"dimension": "provider", "value": None, "callCount": 2, "usage": {"input": None, "output": None, "total": None}}])
                if mutation == "group_usage": groups[0]["usage"]["total"] = 0
                metrics = {"traceCount": 1, "spanCount": 5, "generationCount": 1, "toolCount": 2, "errorCount": trace_errors, "errorRate": float(trace_errors), "usage": {**canonical_usage(source), "cost": {"total": None}}, "coverage": {"billableCalls": 2, "knownUsageCalls": int(chat_values[2] is not None), "knownTokens": 17 if mutation == "known_subtotal" else chat_values[2] or 0}, "groups": groups, "latencyMs": {"mean": 0 if mutation == "latency" else 2, "p95": 2}}
                metrics["reportedUsage"] = {field: {"value": value, "reportedCalls": int(value is not None)} for field, value in zip(("input", "output", "total"), chat_values)}
                metrics["usageByType"] = [
                    {"type": "GENERATION", "callCount": 1, "usage": {"input": None, "output": None, "total": None, "unknownReason": "ambiguous_nested_generations"} if nested_billable else {**chat_usage, "unknownReason": "usage_missing" if chat_values[2] is None else None}, "reportedUsage": copy.deepcopy(metrics["reportedUsage"])},
                    {"type": "EMBEDDING", "callCount": 1, "usage": {"input": None, "output": None, "total": None, "unknownReason": "ambiguous_nested_generations" if nested_billable else "usage_missing"}, "reportedUsage": {field: {"value": None, "reportedCalls": 0} for field in ("input", "output", "total")}},
                ]
                return 200, {"code": 0, "data": metrics}
            raise AssertionError("Unexpected public API route")
        def v1_rows():
            return [{"id": s["span_id"], "traceId": s["trace_id"], "name": s["name"], "parentObservationId": s.get("parent_span_id"), "type": observation_type(s), "model": s["attributes"].get("langfuse.observation.model.name"), "input": json.loads(s["attributes"]["langfuse.observation.input"]) if "langfuse.observation.input" in s["attributes"] else None, "output": json.loads(s["attributes"]["langfuse.observation.output"]) if "langfuse.observation.output" in s["attributes"] else None, "level": s["attributes"].get("langfuse.observation.level", "DEFAULT"), "startTime": "1970-01-01T00:00:01.000000000Z", "endTime": "1970-01-01T00:00:01.002000000Z", "usageDetails": {k: v for k, v in span_usage(s).items() if v is not None}, "usage": {"input": (chat_values[0] or 0) if s["span_id"] == "5" * 16 else 0, "output": (chat_values[1] or 0) if s["span_id"] == "5" * 16 else 0, "total": (chat_values[2] or 0) if s["span_id"] == "5" * 16 else 0, "unit": "TOKENS", "inputCost": None, "outputCost": None, "totalCost": None}} for s in source]
        def trace_fields():
            return {"id": "a" * 32, "timestamp": "1970-01-01T00:00:01.000000000Z", "name": "literal trace", "input": None, "output": report["sessions"][0]["roots"][0]["output"], "sessionId": "s1", "userId": "fake-rag-user", "release": "literal-release", "version": "literal-version", "metadata": {"corpus": "synthetic"}, "tags": ["rag"], "public": False, "environment": "test"}
        return report, exports, {"lf4": target}, query

    def test_native_v1_pagination_sessions_metrics_and_isolation(self):
        report, exports, targets, query = self.platform_input()
        result = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
        self.assertTrue(result["platform_query_verified"], result["failures"])
        self.assertIn("/trace?spaceId=", result["ui_urls"][0])

    def test_error_trace_metrics_follow_the_root_and_reject_wrong_totals(self):
        for root, child, expected in (("UNSET", "ERROR", 0), ("OK", "ERROR", 0), ("ERROR", "OK", 1)):
            with self.subTest(root=root, child=child):
                report, exports, targets, upstream = self.platform_input(
                    root_status=root, child_status=child, trace_errors=expected)
                result = verify_platform_report(report, exports, targets, query=upstream, poll_seconds=0)
                self.assertTrue(result["platform_query_verified"], result["failures"])
                for field in ("errorCount", "errorRate"):
                    def wrong_totals(target, path, **kwargs):
                        status, payload = upstream(target, path, **kwargs)
                        if path.endswith("/traces/metrics"):
                            payload["data"][field] = 1 - expected
                        return status, payload
                    wrong = verify_platform_report(report, exports, targets, query=wrong_totals, poll_seconds=0)
                    for dimension in ("model", "provider"):
                        self.assertFalse(wrong["checks"]["platform.metrics." + dimension + ".errors"], field)

    def test_recovered_child_error_still_requires_exact_native_status_and_v1_level(self):
        for mutation, guard in (("status", "platform.native.0.exact_spans"),
                                ("level", "platform.v1.0.exact_observations")):
            with self.subTest(mutation=mutation):
                report, exports, targets, upstream = self.platform_input(
                    root_status="UNSET", child_status="ERROR", trace_errors=0)
                def query(target, path, **kwargs):
                    status, payload = upstream(target, path, **kwargs)
                    if mutation == "status" and path.endswith("/detail") and status == 200:
                        child = next(s for s in payload["data"]["spans"] if s["span_id"] == "2" * 16)
                        child["status"] = {"code": "UNSET"}
                    if mutation == "level" and path == "/api/public/observations":
                        for child in payload["data"]:
                            if child["id"] == "2" * 16:
                                child["level"] = "DEFAULT"
                    return status, payload
                result = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
                self.assertFalse(result["checks"][guard])

    def test_platform_authenticated_scope_must_be_configured_before_reads(self):
        for field in ('space_id','agent_uuid','agent_product','authenticated_user_id'):
            values=('missing','', ' padded ', True, None)
            if field=='authenticated_user_id': values+=('u'*37,)
            for value in values:
                with self.subTest(field=field,value=value):
                    report,exports,targets,upstream=self.platform_input()
                    if value=='missing': targets['lf4'].pop(field)
                    else: targets['lf4'][field]=value
                    requests=[]
                    def query(*args,requests=requests,upstream=upstream,**kwargs):
                        requests.append((args,kwargs)); return upstream(*args,**kwargs)
                    result=verify_platform_report(report,exports,targets,query=query,poll_seconds=0)
                    self.assertFalse(result['platform_query_verified'])
                    self.assertFalse(result['checks']['platform.target_configured'])
                    self.assertEqual(requests,[])

    def test_wrong_authenticated_user_or_reserved_residue_on_tool_cannot_pass(self):
        for mutation in ('missing','foreign','same_residue','null_residue'):
            with self.subTest(mutation=mutation):
                report,exports,targets,upstream=self.platform_input()
                def query(*args,upstream=upstream,mutation=mutation,**kwargs):
                    status,payload=upstream(*args,**kwargs)
                    if args[1].endswith('/detail') and status==200:
                        span=payload['data']['spans'][1]
                        if mutation=='missing': span['resource'].pop('aivision.user.id')
                        elif mutation=='foreign': span['resource']['aivision.user.id']='foreign-user'
                        else: span['attributes']['user.id']=None if mutation=='null_residue' else 'fake-rag-user'
                    return status,payload
                result=verify_platform_report(report,exports,targets,query=query,poll_seconds=0)
                self.assertFalse(result['platform_query_verified'])
                self.assertFalse(result['checks']['platform.native.0.source_fields'])

    def poll_input(self, transform):
        report, exports, targets, upstream = self.platform_input()
        reads = []
        def query(target, path, **kwargs):
            status, payload = upstream(target, path, **kwargs)
            params = kwargs.get("params") or {}
            if (path.endswith("/detail") and "/spaces/space-literal/" in path and
                    not kwargs.get("key") and params.get("agent_uuid") == "agent-literal" and
                    params.get("from") == report["bounds"]["from_ms"]):
                reads.append(copy.deepcopy(payload))
                return transform(len(reads), status, copy.deepcopy(payload))
            return status, payload
        return report, exports, targets, query, reads

    def test_trace_poll_waits_for_full_span_graph_and_original_fields(self):
        for partial in ("two_of_five", "missing_source_field"):
            def transform(read, status, payload):
                if read == 1:
                    if partial == "two_of_five": payload["data"]["spans"] = payload["data"]["spans"][:2]
                    else: payload["data"]["spans"][1]["attributes"].pop("langfuse.observation.output")
                return status, payload
            report, exports, targets, query, reads = self.poll_input(transform)
            clock = [0.0]
            with self.subTest(partial=partial), patch("verify_rag.time.monotonic", side_effect=lambda: clock[0]), patch(
                    "verify_rag.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
                result = verify_platform_report(report, exports, targets, query=query, poll_seconds=5)
                self.assertTrue(result["platform_query_verified"], result["failures"])
                self.assertEqual(len(reads), 2)

    def test_partial_span_graph_keeps_polling_until_deadline_then_fails_exact_inventory(self):
        def transform(read, status, payload):
            payload["data"]["spans"] = payload["data"]["spans"][:2]
            return status, payload
        report, exports, targets, query, reads = self.poll_input(transform)
        clock = [0.0]
        with patch("verify_rag.time.monotonic", side_effect=lambda: clock[0]), patch(
                "verify_rag.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            result = verify_platform_report(report, exports, targets, query=query, poll_seconds=5)
        self.assertGreater(len(reads), 1)
        self.assertEqual(clock[0], 5)
        self.assertFalse(result["platform_query_verified"])
        self.assertFalse(result["checks"]["platform.native.0.exact_spans"])

    def test_extra_and_duplicate_spans_fail_without_waiting_for_a_cleaner_snapshot(self):
        for mutation in ("extra", "duplicate"):
            def transform(read, status, payload):
                if read == 1:
                    span = copy.deepcopy(payload["data"]["spans"][0])
                    if mutation == "extra": span["span_id"] = "f" * 16
                    payload["data"]["spans"].append(span)
                return status, payload
            report, exports, targets, query, reads = self.poll_input(transform)
            with self.subTest(mutation=mutation), patch("verify_rag.time.sleep") as sleep:
                result = verify_platform_report(report, exports, targets, query=query, poll_seconds=5)
                self.assertFalse(result["platform_query_verified"])
                self.assertFalse(result["checks"]["platform.native.0.exact_spans"])
                self.assertEqual(len(reads), 1)
                sleep.assert_not_called()

    def test_trace_poll_does_not_retry_invalid_request_or_authorization_failures(self):
        for status in (400, 401, 403):
            report, exports, targets, query, reads = self.poll_input(lambda read, previous, payload: (status, {}))
            with self.subTest(status=status), patch("verify_rag.time.sleep") as sleep:
                result = verify_platform_report(report, exports, targets, query=query, poll_seconds=5)
                self.assertFalse(result["platform_query_verified"])
                self.assertFalse(result["checks"]["platform.native.0.http"])
                self.assertEqual(len(reads), 1)
                sleep.assert_not_called()

    def test_real_reader_contract_rejects_dropped_mutated_or_cross_scope_fields(self):
        for mutation in ("missing_span", "parent", "timestamp", "usage", "source_io", "auth_identity", "invalid_auth_leak", "v1_type", "v1_input", "v1_output", "v1_model", "v1_level", "v1_time", "session_scope", "group_usage", "known_subtotal", "latency"):
            report, exports, targets, query = self.platform_input(mutation)
            with self.subTest(mutation=mutation):
                result = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
                self.assertFalse(result["platform_query_verified"])

    def test_v1_trace_public_fields_and_legacy_known_unknown_usage_are_exact(self):
        mutations = ["trace_" + field for field in ("id", "timestamp", "name", "input", "output", "sessionId", "userId", "release", "version", "metadata", "tags", "public", "environment", "htmlPath", "latency", "totalCost", "scores", "observations")]
        mutations += ["legacy_" + kind + "_" + field for kind in ("known", "absent") for field in ("input", "output", "total")]
        mutations += ["legacyalias_" + field for field in ("inputUsage", "outputUsage", "totalUsage")]
        mutations += ["session_user", "session_v1_inventory"]
        for mutation in mutations:
            report, exports, targets, query = self.platform_input(mutation)
            with self.subTest(mutation=mutation):
                wrong = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
                self.assertFalse(wrong["platform_query_verified"])
                guard = "platform.v1.0.trace" if mutation.startswith("trace_") else "platform.v1.session.0.business_user" if mutation == "session_user" else "platform.v1.session.0.trace_inventory" if mutation == "session_v1_inventory" else "platform.v1.0.exact_observations"
                self.assertFalse(wrong["checks"][guard])

    def test_platform_unconfigured_is_explicit_failure(self):
        report, exports = literal_report()
        result = verify_platform_report(report, exports, {})
        self.assertFalse(result["platform_query_verified"])
        self.assertEqual(result["failures"], ["platform.target_configured"])

    def test_reported_subtotal_and_type_metadata_cannot_be_omitted(self):
        for field, guard in (("reportedUsage", "reported_usage"), ("usageByType", "usage_by_type")):
            report, exports, targets, upstream = self.platform_input()
            def query(target, path, **kwargs):
                status, payload = upstream(target, path, **kwargs)
                if path.endswith("/traces/metrics"):
                    payload["data"].pop(field)
                return status, payload
            with self.subTest(field=field):
                result = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
                self.assertFalse(result["platform_query_verified"])
                self.assertFalse(result["checks"]["platform.metrics.model." + guard])

    def test_platform_reader_accepts_zero_partial_and_unknown_measurements_without_filling_embedding(self):
        for values in ((0, 0, 0), (7, None, None), (None, None, None)):
            report, exports, targets, query = self.platform_input(chat_values=values)
            with self.subTest(values=values):
                result = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
                self.assertTrue(result["platform_query_verified"], result["failures"])
                self.assertTrue(result["checks"]["platform.metrics.model.reported_usage"])
                self.assertTrue(result["checks"]["platform.metrics.provider.usage_by_type"])

    def test_boolean_does_not_count_as_a_zero_token_measurement(self):
        report, exports, targets, upstream = self.platform_input(chat_values=(0, 0, 0))
        for field in ("reportedUsage", "usage"):
            def query(target, path, **kwargs):
                status, payload = upstream(target, path, **kwargs)
                if path.endswith("/traces/metrics"):
                    if field == "reportedUsage": payload["data"]["usageByType"][0][field]["input"]["value"] = False
                    else: payload["data"]["usageByType"][0][field]["input"] = False
                return status, payload
            with self.subTest(field=field):
                wrong = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
                self.assertFalse(wrong["platform_query_verified"])
                self.assertFalse(wrong["checks"]["platform.metrics.model.usage_by_type"])

    def test_type_canonical_usage_cannot_lose_billable_ancestor_context(self):
        report, exports, targets, upstream = self.platform_input(nested_billable=True)
        result = verify_platform_report(report, exports, targets, query=upstream, poll_seconds=0)
        self.assertTrue(result["platform_query_verified"], result["failures"])
        def query(target, path, **kwargs):
            status, payload = upstream(target, path, **kwargs)
            if path.endswith("/traces/metrics"):
                payload["data"]["usageByType"][0]["usage"] = {"input": 13, "output": 5, "total": 18}
            return status, payload
        wrong = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
        self.assertFalse(wrong["platform_query_verified"])
        self.assertFalse(wrong["checks"]["platform.metrics.model.usage_by_type"])

    def test_type_unknown_reason_cannot_be_dropped_or_replace_billable_ambiguity(self):
        for values, nested in (((13, 5, 18), False), ((None, None, None), False), ((13, 5, 18), True)):
            report, exports, targets, upstream = self.platform_input(chat_values=values, nested_billable=nested)
            good = verify_platform_report(report, exports, targets, query=upstream, poll_seconds=0)
            self.assertTrue(good["platform_query_verified"], good["failures"])
            expected = "ambiguous_nested_generations" if nested else "usage_missing" if values[2] is None else None
            for marker in ("delete", None, "usage_missing", "ambiguous_nested_generations", "invented"):
                if marker == expected: continue
                def query(target, path, **kwargs):
                    status, payload = upstream(target, path, **kwargs)
                    if path.endswith("/traces/metrics"):
                        usage = payload["data"]["usageByType"][0]["usage"]
                        if marker == "delete": usage.pop("unknownReason")
                        else: usage["unknownReason"] = marker
                    return status, payload
                with self.subTest(values=values, nested=nested, marker=marker):
                    wrong = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
                    self.assertFalse(wrong["platform_query_verified"])
                    self.assertFalse(wrong["checks"]["platform.metrics.model.usage_by_type"])

    def test_subtotal_fields_reject_invented_zero_missing_measurements_and_duplicate_accumulation(self):
        def mutate(metrics, mutation):
            if mutation == "missing_input": metrics["reportedUsage"].pop("input")
            elif mutation == "missing_value": metrics["reportedUsage"]["input"].pop("value")
            elif mutation == "missing_calls": metrics["reportedUsage"]["input"].pop("reportedCalls")
            elif mutation == "invented_zero": metrics["reportedUsage"]["input"]["value"] = 0
            elif mutation == "double_value": metrics["reportedUsage"]["total"]["value"] = 36
            elif mutation == "double_calls": metrics["reportedUsage"]["total"]["reportedCalls"] = 2
            elif mutation == "string_value": metrics["reportedUsage"]["input"]["value"] = "13"
            elif mutation == "string_calls": metrics["reportedUsage"]["input"]["reportedCalls"] = "1"
            elif mutation == "boolean_calls": metrics["reportedUsage"]["input"]["reportedCalls"] = True
        for mutation in ("missing_input", "missing_value", "missing_calls", "invented_zero", "double_value", "double_calls", "string_value", "string_calls", "boolean_calls"):
            report, exports, targets, upstream = self.platform_input()
            def query(target, path, **kwargs):
                status, payload = upstream(target, path, **kwargs)
                if path.endswith("/traces/metrics"): mutate(payload["data"], mutation)
                return status, payload
            with self.subTest(mutation=mutation):
                result = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
                self.assertFalse(result["platform_query_verified"])
                self.assertFalse(result["checks"]["platform.metrics.model.reported_usage"])

    def test_chat_embedding_breakdown_cannot_invent_embedding_billing_or_hide_chat_usage(self):
        def mutate(metrics, mutation):
            rows = metrics["usageByType"]
            if mutation == "reverse": rows.reverse()
            elif mutation == "missing_embedding": rows.pop()
            elif mutation == "duplicate_type": rows.append(copy.deepcopy(rows[0]))
            elif mutation == "double_calls": rows[0]["callCount"] = 2
            elif mutation == "boolean_calls": rows[0]["callCount"] = True
            elif mutation == "chat_unknown": rows[0]["usage"]["total"] = None
            elif mutation == "embedding_zero": rows[1]["usage"]["total"] = 0
            elif mutation == "embedding_invented_measurement": rows[1]["reportedUsage"]["input"] = {"value": 0, "reportedCalls": 1}
            elif mutation == "embedding_missing_null": rows[1]["reportedUsage"]["input"].pop("value")
            elif mutation == "chat_double_value": rows[0]["reportedUsage"]["output"]["value"] = 10
            elif mutation == "missing_type_usage": rows[0].pop("usage")
            elif mutation == "missing_type_reported": rows[0].pop("reportedUsage")
        for mutation in ("reverse", "missing_embedding", "duplicate_type", "double_calls", "boolean_calls", "chat_unknown", "embedding_zero", "embedding_invented_measurement", "embedding_missing_null", "chat_double_value", "missing_type_usage", "missing_type_reported"):
            report, exports, targets, upstream = self.platform_input()
            def query(target, path, **kwargs):
                status, payload = upstream(target, path, **kwargs)
                if path.endswith("/traces/metrics"): mutate(payload["data"], mutation)
                return status, payload
            with self.subTest(mutation=mutation):
                result = verify_platform_report(report, exports, targets, query=query, poll_seconds=0)
                self.assertFalse(result["platform_query_verified"])
                self.assertFalse(result["checks"]["platform.metrics.model.usage_by_type"])


if __name__ == "__main__":
    unittest.main()
