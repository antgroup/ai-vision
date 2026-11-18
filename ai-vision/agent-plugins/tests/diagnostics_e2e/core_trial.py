"""Core Trial transport: original rows in, server-owned semantics out."""

import json
import math
import re
from copy import deepcopy

from calibrate import CalibrationError, HttpTrialExecutor, _owned, trial_result
from corpus import stable_hash


def require(condition, message):
    if not condition:
        raise CalibrationError(message)


def _attribute_text(value):
    # Match the existing OB logical-row boundary, never mutate the original export.
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def source_rows(evidence):
    """Map original exports to existing OB logical rows; retain I/O strings/clock."""
    require(
        isinstance(evidence, dict) and isinstance(evidence.get("exports"), list),
        "Missing exports",
    )
    rows, identities = [], set()
    for export in evidence["exports"]:
        require(export.get("admitted") is True, "Rejected source export")
        for span in export.get("spans", []):
            trace, identity = span.get("trace_id"), span.get("span_id")
            require(
                isinstance(trace, str)
                and re.fullmatch("[0-9a-f]{32}", trace)
                and isinstance(identity, str)
                and re.fullmatch("[0-9a-f]{16}", identity),
                "Invalid source identity",
            )
            require((trace, identity) not in identities, "Duplicate source span")
            identities.add((trace, identity))
            row = {
                key: deepcopy(value)
                for key, value in span.items()
                if key
                not in {
                    "attributes",
                    "resource",
                    "scope_name",
                    "scope_version",
                    "events",
                    "status",
                }
            }
            for carrier, prefix in (
                ("attributes", "attributes."),
                ("resource", "resource.attributes."),
            ):
                values = span.get(carrier, {})
                require(isinstance(values, dict), "Invalid original carrier")
                row.update(
                    {
                        prefix + key: _attribute_text(value)
                        for key, value in values.items()
                    }
                )
            if "code" in span.get("status", {}):
                row["status_code"] = span["status"]["code"]
            rows.append(row)
    require(
        rows and len({row["trace_id"] for row in rows}) == 1,
        "One complete trace per Trial is required",
    )
    require(
        all(
            not row.get("parent_span_id")
            or (row["trace_id"], row["parent_span_id"]) in identities
            for row in rows
        ),
        "Source parent is absent",
    )
    parents = {row["span_id"]: row.get("parent_span_id") for row in rows}
    for identity in parents:
        seen = set()
        current = identity
        while current:
            require(current not in seen, "Source parent cycle")
            seen.add(current)
            current = parents[current]
    return rows


def compare_rows(expected, actual):
    """Extra platform fields are allowed; source identity/I/O loss is not."""
    failures = []
    indexed = {
        (row.get("trace_id"), row.get("span_id")): row
        for row in actual
        if isinstance(row, dict)
    }
    wanted = {(row.get("trace_id"), row.get("span_id")) for row in expected}
    if len(indexed) != len(actual) or set(indexed) != wanted:
        failures.append("source_span_inventory")
    for row in expected:
        found = indexed.get((row["trace_id"], row["span_id"]), {})
        for key, value in row.items():
            observed = found.get(key)
            if key in {"start_time_unix_nano", "end_time_unix_nano"}:
                try:
                    observed = int(observed)
                except (TypeError, ValueError):
                    pass
            if key not in found or stable_hash(observed) != stable_hash(value):
                failures.append(row["span_id"] + ":" + key)
    return {"verified": not failures, "failures": failures}


def compare_authenticated_rows(expected, actual, *, scope):
    """Prove one explicit collector identity strip without claiming raw parity.

    Scope comes from the scoped target and authenticated /auth/me user, never
    from producer attributes. All other original fields still use compare_rows.
    """
    strict = compare_rows(expected, actual)
    fields = {
        "space_id": "resource.attributes.aivision.space.id",
        "agent_uuid": "resource.attributes.aivision.agent.id",
        "agent_product": "resource.attributes.aivision.agent.product",
        "authenticated_user_id": "resource.attributes.aivision.user.id",
    }
    valid_scope = (
        isinstance(scope, dict)
        and set(scope) == set(fields)
        and all(
            type(value) is str and value and value.strip() == value
            for value in scope.values()
        )
        # User.uuid is a CharField(max_length=36), not a UUID normalizer.
        and len(scope["authenticated_user_id"]) <= 36
    )
    if not valid_scope:
        return {
            "verified": False,
            "failures": ["authenticated_scope"],
            "strict_original_rows": strict,
            "identity_transforms": [],
        }
    failures = []
    if not actual:
        failures.append("authenticated_span_inventory")
    for row in actual:
        if not isinstance(row, dict):
            failures.append("authenticated_span_inventory")
            continue
        for field, key in fields.items():
            if type(row.get(key)) is not str or row[key] != scope[field]:
                failures.append(str(row.get("span_id")) + ":" + key)
        if "attributes.user.id" in row:
            failures.append(str(row.get("span_id")) + ":attributes.user.id_retained")
    indexed = {
        (row.get("trace_id"), row.get("span_id")): row
        for row in actual
        if isinstance(row, dict)
    }
    adjusted, transforms = deepcopy(expected), []
    for row in adjusted:
        field = "attributes.user.id"
        if field not in row:
            continue
        original = row[field]
        found = indexed.get((row["trace_id"], row["span_id"]), {})
        aliases = [
            "attributes." + key
            for key in ("gen_ai.user.id", "langfuse.user.id", "ant.uid", "ant.username")
            if type(original) is str
            and original
            and row.get("attributes." + key) == original
            and found.get("attributes." + key) == original
        ]
        if not aliases or field in found:
            failures.append(row["span_id"] + ":attributes.user.id_alias")
            continue
        del row[field]
        transforms.append(
            {
                "field": field,
                "trace_id": row["trace_id"],
                "span_id": row["span_id"],
                "operation": "authenticated_identity_strip",
                "source_value": original,
                "preserved_aliases": aliases,
            }
        )
    semantic = compare_rows(adjusted, actual)
    failures.extend(semantic["failures"])
    return {
        "verified": not failures,
        "failures": failures,
        "strict_original_rows": strict,
        "identity_transforms": transforms,
    }


def transport_timeout(parameters):
    timeout = parameters.get("request_timeout_sec", 60)
    require(
        type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 300,
        "Judge timeout must be finite and at most 300 seconds",
    )
    return 3 * timeout + 3 + 30


def core_binding(metric_key, template):
    variants = {
        "task_completion": {"text_response", "executed_artifact"},
        "report_fidelity": {"observed_fact_comparison"},
        "answer_grounding": {"provided_context"},
    }
    variant = template.get("variant")
    require(
        metric_key in variants
        and variant in variants[metric_key]
        and template.get("identity") == metric_key + "/" + variant,
        "Exactly one canonical variant must be frozen per metric key",
    )
    digest = template.get("definition_digest")
    require(
        isinstance(digest, str)
        and re.fullmatch("[0-9a-f]{64}", digest)
        and isinstance(template.get("version"), str)
        and template["version"],
        "Missing frozen definition identity",
    )
    definition = template.get("definition")
    require(
        isinstance(definition, dict)
        and "definition_digest" not in definition
        and stable_hash(definition) == digest
        and definition.get("key") == metric_key
        and definition.get("variant") == variant
        and definition.get("version") == template["version"]
        and definition.get("mode") == "llm"
        and definition.get("spec", {}).get("prompt") == template.get("prompt"),
        "Frozen full definition/Prompt differs from declared digest",
    )
    return variant


class CoreTrialExecutor:
    def __init__(self, client, *, connection_uuid, provider):
        require(
            isinstance(client.target.get("agent_uuid"), str)
            and client.target["agent_uuid"],
            "Actual scoped Agent target is required",
        )
        require(
            isinstance(connection_uuid, str)
            and re.fullmatch("[A-Za-z0-9_-]+", connection_uuid),
            "Explicit scoped ModelConnection required",
        )
        self.client, self.catalog_path, self.connection_uuid, self.provider = (
            client,
            client.base + "/quick-runs/options",
            connection_uuid,
            provider,
        )

    def __call__(self, evidence, metric_key, *, context):
        scope = context["budget"]
        _owned(scope.owner)
        require(
            scope.scope_active and context["provider_url"] == self.provider.url,
            "Trial must retain the active shared provider scope",
        )
        frozen = context["frozen"]
        template = frozen["templates"][metric_key]
        variant = core_binding(metric_key, template)
        catalog = self.client.request(
            "GET",
            self.catalog_path,
            params={"agent_uuid": self.client.target["agent_uuid"]},
        )
        matches = [
            item
            for item in catalog.get("templates", [])
            if item.get("key") == metric_key and item.get("variant") == variant
        ]
        require(
            len(matches) == 1
            and all(
                matches[0].get(key) == template[key]
                for key in ("variant", "version", "definition_digest")
            ),
            "Current canonical definition drift",
        )
        rows = source_rows(evidence)
        runtime = (
            "codex"
            if evidence.get("app") == "codex"
            else "dsh"
            if evidence.get("app") in {"dsh01", "dsh02"}
            else None
        )
        require(runtime is not None, "Unsupported corpus runtime")
        parameters = deepcopy(frozen["parameters"])
        require(
            set(parameters)
            <= {
                "enable_thinking",
                "temperature",
                "reasoning_effort",
                "max_tokens",
                "request_timeout_sec",
            },
            "Core Prompt/evidence/model override is forbidden",
        )
        body = {
            "builtin_key": metric_key,
            "canonical_variant": variant,
            "spec": {"model_connection_uuid": self.connection_uuid, **parameters},
            "test_input": {
                "trace_id": rows[0]["trace_id"],
                "runtime_type": runtime,
                "spans": rows,
            },
        }
        before = set(scope.identities)

        def parse(response, key):
            result = trial_result(response, key)
            result["diagnosis"] = deepcopy(response)
            return result

        transport = HttpTrialExecutor(
            self.client,
            path=self.client.base + "/diagnosers/llm/trial",
            request_builder=lambda *_args: body,
            result_parser=parse,
            timeout=transport_timeout(parameters),
        )
        result = transport(evidence, metric_key, context=context)
        ordinals = set(scope.identities) - before
        result["attempt_ledger"] = [
            {
                "attempt_number": request["attempt_number"],
                "usage": deepcopy(request.get("usage"))
                if request.get("usage_reported", True)
                else None,
                "reported_cost": request.get("reported_cost"),
            }
            for request in self.provider.requests
            if request.get("attempt_number") in ordinals
        ]
        return result
