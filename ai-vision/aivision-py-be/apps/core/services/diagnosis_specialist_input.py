"""Finite public Specialist inputs. Private policy values never enter errors."""

import json
import math
import re
from copy import deepcopy
from functools import wraps

KEY_VARIANTS = {
    "answer_relevance": {"explicit_current_text", "recorded_conversation"},
    "followup_reasonableness": {"recorded_conversation"},
    "numeric_consistency": {"decimal_binary", "decimal_binary_same_unit"},
    "response_language": {"configured_final_text"},
    "protected_content_exposure": {"configured_exact_markers"},
    "structured_output_contract": {"json_object_fields"},
    "required_tool_execution": {"observed_tool_completion"},
}
UNAVAILABLE = {
    "subject_consistency",
    "provided_context_use",
    "data_request_fulfilment",
}
LLM_KEYS = {"answer_relevance", "followup_reasonableness", "response_language"}
ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
DIAGNOSER_ID = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
TRACE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
POLICY_ID = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
JSON_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
JSON_FIELD_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}
TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")


class SpecialistError(Exception):
    def __init__(self, key="SPECIALIST_INPUT_INVALID", status=422):
        self.key, self.status = key, status
        super().__init__(key)


def reject():
    raise SpecialistError()


def object_fields(value, required, optional=()):
    if (
        type(value) is not dict
        or not set(required) <= value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        reject()
    return value


def text(value, limit, *, empty=False, pattern=None):
    if (
        type(value) is not str
        or len(value) > limit
        or (not empty and not value.strip())
        or (pattern and not pattern.fullmatch(value))
    ):
        reject()
    return value


def bounded_json(value, *, max_depth=8):
    def visit(item, depth=0):
        if depth > max_depth:
            reject()
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                reject()
            for child in item.values():
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        elif item is not None and type(item) not in {str, bool, int, float}:
            reject()
        elif type(item) is float and not math.isfinite(item):
            reject()

    visit(value)
    try:
        if (
            len(
                json.dumps(
                    value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ).encode("utf-8")
            )
            > 32768
        ):
            reject()
    except (ValueError, TypeError, UnicodeError):
        reject()


def validate_options(value):
    object_fields(
        value,
        (),
        {
            "enable_thinking",
            "temperature",
            "reasoning_effort",
            "max_tokens",
            "request_timeout_sec",
        },
    )
    if (
        "enable_thinking" in value
        and value["enable_thinking"] is not None
        and type(value["enable_thinking"]) is not bool
    ):
        reject()
    if "temperature" in value:
        n = value["temperature"]
        if type(n) not in {int, float} or not math.isfinite(n) or not 0 <= n <= 2:
            reject()
    if "max_tokens" in value and (
        type(value["max_tokens"]) is not int or not 1 <= value["max_tokens"] <= 8192
    ):
        reject()
    if "request_timeout_sec" in value:
        n = value["request_timeout_sec"]
        if type(n) not in {int, float} or not math.isfinite(n) or not 0 < n <= 300:
            reject()
    if "reasoning_effort" in value:
        n = value["reasoning_effort"]
        if not (type(n) is int and 1 <= n <= 100) and not (
            type(n) is str
            and n in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        ):
            reject()


def validate_policy(policy, key=None, variant=None, *, allow_preserve=False):
    bounded_json(policy, max_depth=6)
    object_fields(
        policy,
        {"kind"},
        {
            "context_mode",
            "checks",
            "required_language",
            "scope",
            "allowed_terms",
            "match",
            "markers",
            "fields",
            "tool_name",
        },
    )
    kind = policy["kind"]
    if type(kind) is not str:
        reject()
    if kind == "preserve_existing_policy":
        object_fields(policy, {"kind"})
        if not allow_preserve:
            reject()
        return None
    derived = next((name for name in KEY_VARIANTS if kind == name + "_v1"), None)
    if derived is None or (key is not None and key != derived):
        reject()
    if derived in {"answer_relevance", "followup_reasonableness"}:
        object_fields(policy, {"kind", "context_mode"})
        if policy["context_mode"] not in KEY_VARIANTS[derived] or (
            variant and policy["context_mode"] != variant
        ):
            reject()
    elif derived == "numeric_consistency":
        object_fields(policy, {"kind", "checks"})
        checks = policy["checks"]
        if type(checks) is not list or not 1 <= len(checks) <= 8:
            reject()
        seen = set()
        for check in checks:
            object_fields(check, {"id", "expression", "result", "precision", "unit"})
            ident = text(check["id"], 32, pattern=POLICY_ID)
            if ident in seen:
                reject()
            seen.add(ident)
            for selector, field in (
                (check["expression"], "current_request"),
                (check["result"], "final_output"),
            ):
                object_fields(selector, {"field", "prefix", "suffix"})
                if selector["field"] != field:
                    reject()
                text(selector["prefix"], 128, empty=True)
                text(selector["suffix"], 128, empty=True)
            precision = check["precision"]
            object_fields(precision, {"mode"}, {"scale", "rounding"})
            if precision["mode"] == "exact":
                object_fields(precision, {"mode"})
            elif precision["mode"] == "quantized":
                object_fields(precision, {"mode", "scale", "rounding"})
                if (
                    type(precision["scale"]) is not int
                    or not 0 <= precision["scale"] <= 12
                    or precision["rounding"]
                    not in {"ROUND_HALF_EVEN", "ROUND_HALF_UP", "ROUND_DOWN"}
                ):
                    reject()
            else:
                reject()
            unit = check["unit"]
            if unit is not None:
                text(unit, 16)
                if any(char.isspace() for char in unit):
                    reject()
            if variant == "decimal_binary" and unit is not None:
                reject()
            if variant == "decimal_binary_same_unit" and unit is None:
                reject()
    elif derived == "response_language":
        object_fields(policy, {"kind", "required_language", "scope", "allowed_terms"})
        if (
            policy["required_language"]
            not in {"zh-CN", "zh-TW", "en", "ja", "ko", "fr", "de", "es"}
            or policy["scope"] != "final_natural_language"
        ):
            reject()
        terms = policy["allowed_terms"]
        if type(terms) is not list or len(terms) > 32:
            reject()
        for term in terms:
            text(term, 80)
        if len(set(terms)) != len(terms) or sum(map(len, terms)) > 2048:
            reject()
    elif derived == "structured_output_contract":
        object_fields(policy, {"kind", "fields"})
        fields = policy["fields"]
        if type(fields) is not list or not 1 <= len(fields) <= 16:
            reject()
        names = set()
        for field in fields:
            object_fields(field, {"name", "type"})
            name = text(field["name"], 64, pattern=JSON_FIELD_NAME)
            if name in names or text(field["type"], 7) not in JSON_FIELD_TYPES:
                reject()
            names.add(name)
    elif derived == "required_tool_execution":
        object_fields(policy, {"kind", "tool_name"})
        text(policy["tool_name"], 128, pattern=TOOL_NAME)
    elif derived == "protected_content_exposure":
        object_fields(policy, {"kind", "scope", "match", "markers"})
        if (
            policy["scope"] != "final_visible_text"
            or policy["match"] != "literal_exact"
        ):
            reject()
        markers = policy["markers"]
        if type(markers) is not list or not 1 <= len(markers) <= 64:
            reject()
        ids = set()
        values = set()
        protected = []
        public = []
        for marker in markers:
            object_fields(marker, {"id", "value", "visibility"})
            ident = text(marker["id"], 32, pattern=POLICY_ID)
            value = text(marker["value"], 256)
            if (
                ident in ids
                or value in values
                or marker["visibility"] not in {"protected", "public"}
            ):
                reject()
            ids.add(ident)
            values.add(value)
            (protected if marker["visibility"] == "protected" else public).append(value)
        if (
            not protected
            or sum(map(len, values)) > 8192
            or any(a in b or b in a for a in protected for b in public)
        ):
            reject()
    return derived


def validate_configuration(
    value, key=None, variant=None, *, allow_preserve=False, allow_model_omission=False
):
    object_fields(value, {"policy"}, {"model_connection_uuid", "llm_options"})
    derived = validate_policy(
        value["policy"], key, variant, allow_preserve=allow_preserve
    )
    resolved = key or derived
    if resolved in LLM_KEYS:
        # PATCH omission retains the saved model; create/draft still require it.
        if "model_connection_uuid" in value or not allow_model_omission:
            text(value.get("model_connection_uuid"), 64, pattern=ID)
        if "llm_options" in value:
            validate_options(value["llm_options"])
    elif resolved is not None:
        if "model_connection_uuid" in value or "llm_options" in value:
            reject()
    else:
        # Preserve operations do not reveal the key. AL validates exact saved identity.
        if "model_connection_uuid" in value:
            text(value["model_connection_uuid"], 64, pattern=ID)
        if "llm_options" in value:
            validate_options(value["llm_options"])


def finite_input(function):
    @wraps(function)
    def wrapped(body):
        try:
            return function(body)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise SpecialistError() from None

    return wrapped


@finite_input
def normalize_sync(body):
    bounded_json(body)
    object_fields(body, {"key", "variant", "configuration"})
    key = text(body["key"], 64)
    variant = text(body["variant"], 64)
    if key in UNAVAILABLE:
        raise SpecialistError("SPECIALIST_UNAVAILABLE")
    if key not in KEY_VARIANTS or variant not in KEY_VARIANTS[key]:
        reject()
    validate_configuration(body["configuration"], key, variant)
    return deepcopy(body)


@finite_input
def normalize_configuration(body):
    bounded_json(body)
    object_fields(body, {"expected_configuration_digest", "configuration"})
    text(body["expected_configuration_digest"], 64, pattern=DIGEST)
    validate_configuration(
        body["configuration"], allow_preserve=True, allow_model_omission=True
    )
    return deepcopy(body)


@finite_input
def normalize_trial(body):
    bounded_json(body)
    object_fields(body, {"trace_id"}, {"diagnoser_uuid", "draft"})
    text(body["trace_id"], 128, pattern=TRACE_ID)
    if ("diagnoser_uuid" in body) == ("draft" in body):
        reject()
    if "draft" in body:
        normalize_sync(body["draft"])
    else:
        text(body["diagnoser_uuid"], 32, pattern=DIAGNOSER_ID)
    return deepcopy(body)
