"""Public budget observations, kept independent of quality and TaskStats."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class JudgeRequestSummary:
    reserved_slots: int = 0
    response_confirmed_attempts: int = 0
    unknown_attempts: int = 0
    request_error_attempts: int = 0
    total_cap: int = 30
    per_case_cap: int = 3
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    usage_known_attempts: dict[str, int] = field(default_factory=dict)
    known_token_totals: dict[str, int] = field(default_factory=dict)


def validate_ledger(ledger: dict | None) -> dict:
    if ledger is None:
        return {"schema_version": 1, "entries": []}
    if (
        not isinstance(ledger, dict)
        or set(ledger) != {"schema_version", "entries"}
        or ledger["schema_version"] != 1
        or not isinstance(ledger["entries"], list)
        or len(ledger["entries"]) > 30
    ):
        raise ValueError("unsupported Judge request ledger")
    tickets = set()
    for ordinal, entry in enumerate(ledger["entries"], 1):
        if (
            not isinstance(entry, dict)
            or entry.get("ordinal") != ordinal
            or not isinstance(entry.get("request_uuid"), str)
            or entry["request_uuid"] in tickets
            or entry.get("outcome")
            not in {"reserved", "dispatch_unknown", "response", "request_error"}
            or not isinstance(entry.get("logical_case_key"), str)
        ):
            raise ValueError("invalid Judge request ledger entry")
        tickets.add(entry["request_uuid"])
        usage = entry.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError("invalid Judge usage")
        fields = usage.get("known_fields", [])
        if (
            not isinstance(fields, list)
            or len(fields) != len(set(fields))
            or any(k not in {"input_tokens", "output_tokens", "total_tokens"} for k in fields)
        ):
            raise ValueError("invalid Judge usage coverage")
        for key in fields:
            value = usage.get(key)
            if type(value) is not int or not 0 <= value <= 2**63 - 1:
                raise ValueError("invalid Judge token count")
    return ledger


def judge_request_summary(ledger: dict | None) -> JudgeRequestSummary:
    entries = validate_ledger(ledger)["entries"]
    counts, known_totals, aggregate = {}, {}, {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        known = [
            e["usage"][key] for e in entries if key in e.get("usage", {}).get("known_fields", [])
        ]
        counts[key] = len(known)
        known_totals[key] = sum(known)
        aggregate[key] = sum(known) if entries and len(known) == len(entries) else None
    return JudgeRequestSummary(
        reserved_slots=len(entries),
        response_confirmed_attempts=sum(e["outcome"] == "response" for e in entries),
        request_error_attempts=sum(e["outcome"] == "request_error" for e in entries),
        unknown_attempts=sum(e["outcome"] != "response" for e in entries),
        usage_known_attempts=counts,
        known_token_totals=known_totals,
        **aggregate,
    )
