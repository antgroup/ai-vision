import type { RuleMaterials } from "./enhancedRuleModel"

export interface EnhancedRuleExample {
  id: string
  group: "starter" | "advanced"
  libraries: string[]
  metrics: string[]
  code: string
  input: { spans: { attributes: Record<string, unknown> }[] }
  materials: RuleMaterials
}

// Every example includes complete synthetic input; users map example.* to their own Trace fields.
export const ENHANCED_EXAMPLES: EnhancedRuleExample[] = [
  {
    id: "json",
    group: "starter",
    libraries: ["jsonschema"],
    metrics: ["schema_valid"],
    code: `import json
import jsonschema


def evaluate(trace, context):
    # Map this synthetic field to the observed Agent output in your own rule.
    spans = trace.get("spans") or []
    actual = spans[0].get("attributes", {}).get("example.json_output") if spans else None
    schema = json.loads(context.read_text("schema.json"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(actual))
    return {"metrics": [{
        "name": "schema_valid", "verdict": "bad" if errors else "good",
        "reason": "Validate required fields, types, citation IDs and unexpected properties",
        "evidence": {"expected": schema, "actual": actual, "failures": [
            {"path": "/".join(map(str, error.path)), "reason": error.message}
            for error in errors[:10]
        ]},
    }]}
`,
    input: {
      spans: [
        {
          attributes: {
            "example.json_output": {
              answer: "42 items remain in stock.",
              citations: ["stock-2026"],
            },
          },
        },
      ],
    },
    materials: {
      files: [
        {
          path: "schema.json",
          encoding: "utf-8",
          content:
            '{\n  "type": "object",\n  "required": [\n    "answer",\n    "citations"\n  ],\n  "additionalProperties": false,\n  "properties": {\n    "answer": {\n      "type": "string",\n      "minLength": 1\n    },\n    "citations": {\n      "type": "array",\n      "minItems": 1,\n      "uniqueItems": true,\n      "items": {\n        "type": "string",\n        "minLength": 1\n      }\n    }\n  }\n}\n',
        },
      ],
    },
  },
  {
    id: "table",
    group: "starter",
    libraries: ["pandas", "openpyxl"],
    metrics: ["csv_total", "xlsx_match"],
    code: `import io
import math
import pandas as pd
from openpyxl import load_workbook


def evaluate(trace, context):
    spans = trace.get("spans") or []
    actual = spans[0].get("attributes", {}) if spans else {}
    sales = pd.read_csv(io.StringIO(context.read_text("sales.csv")))
    if sales.empty or "amount" not in sales:
        raise ValueError("Reference sales.csv must contain non-empty amount rows")
    amounts = pd.to_numeric(sales["amount"], errors="raise")
    if amounts.isna().any() or not all(math.isfinite(float(value)) for value in amounts):
        raise ValueError("Every reference amount must be a finite number")
    expected_total = math.fsum(float(value) for value in amounts)
    if not math.isfinite(expected_total):
        raise ValueError("The reference total must be finite")
    book = load_workbook(context.material_path("reference.xlsx"), read_only=True, data_only=True)
    try:
        expected_cell = book.active["B2"].value
    finally:
        book.close()
    if type(expected_cell) not in (int, float) or not math.isfinite(expected_cell):
        raise ValueError("Reference workbook B2 must contain a finite numeric value")
    total = actual.get("example.total")
    valid_total = type(total) in (int, float) and math.isfinite(total)
    total_matches = valid_total and abs(total - expected_total) < 0.000001
    cell = actual.get("example.cell_b2")
    cell_matches = type(cell) in (int, float) and cell == expected_cell
    return {"metrics": [
        {"name": "csv_total", "verdict": "good" if total_matches else "bad",
         "reason": "Recompute the reported total from CSV rows",
         "evidence": {"expected": expected_total, "actual": total}},
        {"name": "xlsx_match", "verdict": "good" if cell_matches else "bad",
         "reason": "Compare the reported workbook value with the reference cell",
         "evidence": {"path": "reference.xlsx:B2", "expected": expected_cell, "actual": cell}},
    ]}
`,
    input: {
      spans: [
        {
          attributes: {
            "example.total": 42,
            "example.cell_b2": 42,
          },
        },
      ],
    },
    materials: {
      files: [
        {
          path: "sales.csv",
          encoding: "utf-8",
          content: "item,amount\nA,12\nB,30\n",
        },
        {
          path: "reference.xlsx",
          encoding: "base64",
          content:
            "UEsDBBQAAAAIACh4Y10cdPw42AAAAPYBAAATAAAAW0NvbnRlbnRfVHlwZXNdLnhtbK2RvU4DMQzHX+WUtWpcGDqgXhfKCgy8gJvz9aLLl2K3XN++uVAYUIGFyUr+Hz9L3rydE3EzeRe4VYNIegBgM5BH1jFRKEofs0cpz3yAhGbEA8H9arUGE4NQkKXMHWq72VGPRyfN01S+2cbQqkyOVfP4YZxZrcKUnDUoRYdT6L5RlleCLsnq4cEmXhSDgpuEWfkZcM29nChn21Hzilme0RcXTA7eYx73MY7695IbW8a+t4a6aI6+RDSnTNjxQCTe6Tq1RxsWf/OrmaGOu39e5Kv/cw+o595eAFBLAwQUAAAACAAoeGNd/luGcooAAADwAAAACwAAAF9yZWxzLy5yZWxzjc8xDsIwDAXQq1Q+QF0YGFDaiaUr4gImddqqTRw5QZTbk7EgBkbrf70vmyuvlGcJaZpjqja/htTClHM8IyY7sadUS+RQEifqKZdTR4xkFxoZj01zQt0b0Jm9WfVDC9oPB6hur8j/2OLcbPki9uE55B8TX40ik46cW9hWfIoud5GlLihgZ/Djwe4NUEsDBBQAAAAIACh4Y11FwPuelAAAAOcAAAAPAAAAeGwvd29ya2Jvb2sueG1sjY8xDsIwDEWvEvkAuGVgqNpOLKzcIKQOidrEkR0Ex6dq6c5k///l9+X+zTI/mGfzSUvWAUKtpUNUFyhZPXGhvCaeJdm6SnmiFiE7aSCqacFz01ww2ZhhJ3TyD4O9j46u7F6Jct0hQoutkbOGWBTGfmvQ3zTZJhrgTp6EsiMwm32bBmjBSBfXRW5TCzj2eFzi8dz4BVBLAwQUAAAACAAoeGNdtlSqz44AAADxAAAAGgAAAHhsL19yZWxzL3dvcmtib29rLnhtbC5yZWxzjc89DsIwDAXgq1Q5QN0yMKAmE0tXxAWi1G2iNj+yjYDbEzGgIjEwWX6WvicPF9yshJzYh8LNI26JtfIi5QTAzmO03OaCqV7mTNFKXWmBYt1qF4RD1x2B9oYyw95sxkkrGqdeNddnwX/sPM/B4Tm7W8QkPyrgnmlljygVtbSgaPWJGN6jb6uqwAzw9aF5AVBLAwQUAAAACAAoeGNdEB0+W3oAAACeAAAAGAAAAHhsL3dvcmtzaGVldHMvc2hlZXQxLnhtbE2OTQoCMQyFr1J6ADMUcSFtF+JFQo1WnP6Qhpk5vplZiIs88r68B/Fr48/IRGK2MtcRbBbpV4CRMhUcp9ap6uXZuKCo5ReMzoSPo1RmcNN0gYLvaqM/2B0Fo+e2Gg7WKU37cnPWSLB7aoln52GJHpKOBlX/mvB7KX4BUEsBAhQDFAAAAAgAKHhjXRx0/DjYAAAA9gEAABMAAAAAAAAAAAAAAIABAAAAAFtDb250ZW50X1R5cGVzXS54bWxQSwECFAMUAAAACAAoeGNd/luGcooAAADwAAAACwAAAAAAAAAAAAAAgAEJAQAAX3JlbHMvLnJlbHNQSwECFAMUAAAACAAoeGNdRcD7npQAAADnAAAADwAAAAAAAAAAAAAAgAG8AQAAeGwvd29ya2Jvb2sueG1sUEsBAhQDFAAAAAgAKHhjXbZUqs+OAAAA8QAAABoAAAAAAAAAAAAAAIABfQIAAHhsL19yZWxzL3dvcmtib29rLnhtbC5yZWxzUEsBAhQDFAAAAAgAKHhjXRAdPlt6AAAAngAAABgAAAAAAAAAAAAAAIABQwMAAHhsL3dvcmtzaGVldHMvc2hlZXQxLnhtbFBLBQYAAAAABQAFAEUBAADzAwAAAAA=",
        },
      ],
    },
  },
  {
    id: "python",
    group: "advanced",
    libraries: ["pytest"],
    metrics: ["reference_tests"],
    code: `def evaluate(trace, context):
    spans = trace.get("spans") or []
    source = spans[0].get("attributes", {}).get("example.generated_code") if spans else None
    if not isinstance(source, str) or not source.strip():
        return {"metrics": [{"name": "reference_tests", "verdict": "bad",
                             "reason": "Missing generated Python source"}]}
    # The reference tests import candidate.normalize_tags from this generated source.
    result = context.run_python_tests(source, "tests/test_candidate.py")
    return {"metrics": [{
        "name": "reference_tests", "verdict": result["verdict"].lower(),
        "reason": "Run normalization, ordering, invalid-input and Unicode reference tests",
        "evidence": result,
    }]}
`,
    input: {
      spans: [
        {
          attributes: {
            "example.generated_code":
              'def normalize_tags(tags):\n    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):\n        raise ValueError("tags must be a list of strings")\n    return list(dict.fromkeys(tag.strip().casefold() for tag in tags if tag.strip()))\n',
          },
        },
      ],
    },
    materials: {
      files: [
        {
          path: "tests/test_candidate.py",
          encoding: "utf-8",
          content:
            'import pytest\nfrom candidate import normalize_tags\n\n\n@pytest.mark.parametrize("tags, expected", [\n    (["  Python ", "python", "DATA"], ["python", "data"]),\n    (["B", "a", "b", "c"], ["b", "a", "c"]),\n    (["", "  ", "\t"], []),\n    ([], []),\n    (["Straße", "STRASSE", "数据"], ["strasse", "数据"]),\n])\ndef test_normalization_and_first_seen_order(tags, expected):\n    original = list(tags)\n    assert normalize_tags(tags) == expected\n    assert tags == original, "The candidate must not mutate its input"\n\n\n@pytest.mark.parametrize("tags", [None, "python", [1], ["ok", None]])\ndef test_invalid_input(tags):\n    with pytest.raises(ValueError):\n        normalize_tags(tags)\n',
        },
      ],
    },
  },
  {
    id: "orders",
    group: "advanced",
    libraries: ["pandas"],
    metrics: ["regional_totals", "net_revenue"],
    code: `import io
import pandas as pd


def evaluate(trace, context):
    orders = pd.read_csv(io.StringIO(context.read_text("orders.csv")), dtype={"order_id": "string", "region": "string", "gross_cents": "string"})
    refunds = pd.read_csv(io.StringIO(context.read_text("refunds.csv")), dtype={"order_id": "string", "refund_cents": "string"})
    if not {"order_id", "region", "gross_cents"} <= set(orders.columns) or orders.empty:
        raise ValueError("Reference orders require non-empty order_id, region and gross_cents columns")
    if not {"order_id", "refund_cents"} <= set(refunds.columns):
        raise ValueError("Reference refunds require order_id and refund_cents columns")
    for frame, columns in ((orders, ("order_id", "region")), (refunds, ("order_id",))):
        for column in columns:
            if frame[column].isna().any() or frame[column].str.strip().eq("").any():
                raise ValueError("Reference IDs and regions must not be blank")
    if orders["order_id"].duplicated().any():
        raise ValueError("Reference order IDs must be unique")
    if not set(refunds["order_id"]) <= set(orders["order_id"]):
        raise ValueError("Every reference refund must identify an existing order")
    for frame, column in ((orders, "gross_cents"), (refunds, "refund_cents")):
        # Keep cents as text until validation: float inference can round large integers.
        raw = frame[column]
        if raw.isna().any() or not raw.str.fullmatch(r"[0-9]+").all():
            raise ValueError("Reference cents must use non-negative integer digits")
        values = pd.to_numeric(raw, errors="raise")
        if sum(int(value) for value in values) > 9223372036854775807:
            raise ValueError("Reference cents exceed the exact integer aggregation range")
        frame[column] = values.astype("int64")
    # Aggregate repeated refunds before joining, so orders are not double counted.
    refunds = refunds.groupby("order_id", as_index=False)["refund_cents"].sum()
    # Nullable integers preserve exact cents when an order has no refund row.
    refunds["refund_cents"] = refunds["refund_cents"].astype("Int64")
    ledger = orders.merge(refunds, on="order_id", how="left", validate="one_to_one")
    ledger["refund_cents"] = ledger["refund_cents"].fillna(0).astype("int64")
    ledger["net_cents"] = ledger["gross_cents"] - ledger["refund_cents"]
    if (ledger["net_cents"] < 0).any():
        raise ValueError("Reference refunds must not exceed an order's gross amount")
    expected_regions = {str(key): int(value) for key, value in
                        ledger.groupby("region")["net_cents"].sum().items()}
    expected_net = int(ledger["net_cents"].sum())
    spans = trace.get("spans") or []
    report = spans[0].get("attributes", {}).get("example.report") if spans else None
    report = report if isinstance(report, dict) else {}
    regions = report.get("regions")
    region_shape = isinstance(regions, dict) and all(type(v) is int for v in regions.values())
    region_match = region_shape and regions == expected_regions
    net = report.get("net_revenue_cents")
    net_match = type(net) is int and net == expected_net
    return {"metrics": [
        {"name": "regional_totals", "verdict": "good" if region_match else "bad",
         "reason": "Reconcile every region after aggregating partial refunds",
         "evidence": {"expected": expected_regions, "actual": regions}},
        {"name": "net_revenue", "verdict": "good" if net_match else "bad",
         "reason": "Verify the net total using integer cents",
         "evidence": {"expected": expected_net, "actual": net, "order_count": len(ledger)}},
    ]}
`,
    input: {
      spans: [
        {
          attributes: {
            "example.report": {
              regions: {
                East: 2100,
                West: 2500,
              },
              net_revenue_cents: 4600,
            },
          },
        },
      ],
    },
    materials: {
      files: [
        {
          path: "orders.csv",
          encoding: "utf-8",
          content:
            "order_id,region,gross_cents\no1,East,2400\no2,West,3000\no3,East,600\n",
        },
        {
          path: "refunds.csv",
          encoding: "utf-8",
          content: "order_id,refund_cents\no1,700\no1,200\no2,500\n",
        },
      ],
    },
  },
  {
    id: "business",
    group: "advanced",
    libraries: ["pydantic"],
    metrics: ["business_valid"],
    code: `from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class RefundDecision(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    status: Literal["approved", "rejected"]
    order_total_cents: int = Field(gt=0)
    refund_cents: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_business_rules(self):
        if not self.reason.strip():
            raise ValueError("A non-blank reason is required")
        if self.refund_cents > self.order_total_cents:
            raise ValueError("Refund cannot exceed the order total")
        if self.status == "rejected" and self.refund_cents != 0:
            raise ValueError("A rejected decision must have zero refund")
        if self.status == "approved" and self.refund_cents == 0:
            raise ValueError("An approved decision must have a positive refund")
        return self


def evaluate(trace, context):
    spans = trace.get("spans") or []
    actual = spans[0].get("attributes", {}).get("example.refund_decision") if spans else None
    try:
        decision = RefundDecision.model_validate(actual)
        failures = []
        actual = decision.model_dump()
    except ValidationError as error:
        # Omit exception objects in ctx so all evidence remains JSON serializable.
        failures = error.errors(include_url=False, include_context=False, include_input=False)[:10]
    return {"metrics": [{
        "name": "business_valid", "verdict": "bad" if failures else "good",
        "reason": "Check strict field types and cross-field refund constraints",
        "evidence": {"actual": actual, "failures": failures},
    }]}
`,
    input: {
      spans: [
        {
          attributes: {
            "example.refund_decision": {
              status: "approved",
              order_total_cents: 10000,
              refund_cents: 2500,
              reason: "One of four items was damaged.",
            },
          },
        },
      ],
    },
    materials: {
      files: [],
    },
  },
  {
    id: "retrieval",
    group: "advanced",
    libraries: ["numpy"],
    metrics: ["retrieval_recall", "ranking_quality", "citation_coverage"],
    code: `import numpy as np


def evaluate(trace, context):
    # These are relevance labels supplied by a reference set, not semantic judgments.
    reference = context.expected
    relevant = set(reference["relevant_ids"])
    k = reference["k"]
    spans = trace.get("spans") or []
    actual = spans[0].get("attributes", {}) if spans else {}
    retrieved = actual.get("example.retrieved_ids")
    citations = actual.get("example.cited_ids")
    valid_retrieval = (isinstance(retrieved, list) and 0 < len(retrieved) <= 100
                       and all(isinstance(item, str) for item in retrieved)
                       and len(set(retrieved)) == len(retrieved))
    ranked = retrieved[:k] if valid_retrieval else []
    hits = np.asarray([1.0 if item in relevant else 0.0 for item in ranked])
    recall = len(set(ranked) & relevant) / len(relevant)
    discounts = np.log2(np.arange(len(ranked), dtype=float) + 2.0)
    dcg = float((hits / discounts).sum())
    ideal_count = min(len(relevant), k)
    ideal_dcg = float((1.0 / np.log2(np.arange(ideal_count, dtype=float) + 2.0)).sum())
    ndcg = dcg / ideal_dcg
    valid_citations = (isinstance(citations, list) and 0 < len(citations) <= 100
                       and all(isinstance(item, str) for item in citations)
                       and len(set(citations)) == len(citations))
    citation_set = set(citations) if valid_citations else set()
    supported = valid_citations and valid_retrieval and citation_set <= set(ranked)
    coverage = len(citation_set & relevant) / len(relevant)
    return {"metrics": [
        {"name": "retrieval_recall", "verdict": "good" if valid_retrieval and recall >= reference["min_recall"] else "bad",
         "reason": "Measure relevant-document recall in the first k results",
         "evidence": {"actual": recall, "expected": reference["min_recall"], "k": k, "ranked_ids": ranked}},
        {"name": "ranking_quality", "verdict": "good" if valid_retrieval and ndcg >= reference["min_ndcg"] else "bad",
         "reason": "Measure binary-relevance nDCG, penalizing late relevant results",
         "evidence": {"actual": ndcg, "expected": reference["min_ndcg"]}},
        {"name": "citation_coverage", "verdict": "good" if supported and coverage >= reference["min_citation_coverage"] else "bad",
         "reason": "Check citation IDs against retrieved and reference IDs; this does not prove answer faithfulness",
         "evidence": {"actual": coverage, "expected": reference["min_citation_coverage"], "supported_ids": bool(supported)}},
    ]}
`,
    input: {
      spans: [
        {
          attributes: {
            "example.retrieved_ids": ["doc-a", "doc-b", "doc-x"],
            "example.cited_ids": ["doc-a", "doc-b"],
          },
        },
      ],
    },
    materials: {
      files: [],
      expected_json:
        '{\n  "relevant_ids": [\n    "doc-a",\n    "doc-b"\n  ],\n  "k": 3,\n  "min_recall": 1,\n  "min_ndcg": 0.9,\n  "min_citation_coverage": 1\n}\n',
    },
  },
  {
    id: "numerical",
    group: "advanced",
    libraries: ["numpy"],
    metrics: ["tolerance", "error_budget"],
    code: `import numpy as np


def evaluate(trace, context):
    reference = context.expected
    expected = np.asarray(reference["values"], dtype=float)
    spans = trace.get("spans") or []
    values = spans[0].get("attributes", {}).get("example.predictions") if spans else None
    valid = (isinstance(values, list) and len(values) == len(expected)
             and all(type(value) in (int, float) for value in values))
    actual = np.asarray(values, dtype=float) if valid else np.asarray([], dtype=float)
    valid = bool(valid and actual.shape == expected.shape and np.isfinite(actual).all())
    if not valid:
        return {"metrics": [{"name": name, "verdict": "bad",
                             "reason": "Expected a finite numeric vector with the reference shape",
                             "evidence": {"expected_count": len(expected), "actual": values}}
                            for name in ("tolerance", "error_budget")]}
    with np.errstate(over="ignore", invalid="ignore"):
        errors = actual - expected
    if not np.isfinite(errors).all():
        return {"metrics": [{"name": name, "verdict": "bad",
                             "reason": "The difference exceeds the finite numeric range",
                             "evidence": {"expected": expected.tolist(), "actual": actual.tolist()}}
                            for name in ("tolerance", "error_budget")]}
    matches = np.isclose(actual, expected, rtol=reference["rtol"], atol=reference["atol"])
    # Scale before aggregation: finite large candidates must produce BAD, not Infinity evidence.
    scale = float(np.abs(errors).max())
    normalized = errors / scale if scale else errors
    mae = float(np.abs(normalized).mean()) * scale
    rmse = float(np.sqrt(np.mean(normalized ** 2))) * scale
    failed = np.flatnonzero(~matches).tolist()
    return {"metrics": [
        {"name": "tolerance", "verdict": "good" if bool(matches.all()) else "bad",
         "reason": "Check each point with absolute and relative tolerance",
         "evidence": {"failures": [{"index": index, "expected": float(expected[index]), "actual": float(actual[index])} for index in failed[:10]],
                      "rtol": reference["rtol"], "atol": reference["atol"]}},
        {"name": "error_budget", "verdict": "good" if rmse <= reference["max_rmse"] else "bad",
         "reason": "Measure aggregate error so local tolerances cannot hide global drift",
         "evidence": {"mae": mae, "rmse": rmse, "expected": reference["max_rmse"], "actual": rmse}},
    ]}
`,
    input: {
      spans: [
        {
          attributes: {
            "example.predictions": [10.01, 19.98, 30.01, 39.99],
          },
        },
      ],
    },
    materials: {
      files: [],
      expected_json:
        '{\n  "values": [\n    10,\n    20,\n    30,\n    40\n  ],\n  "rtol": 0.001,\n  "atol": 0.02,\n  "max_rmse": 0.02\n}\n',
    },
  },
]
