import base64
import hashlib
import json

import pytest


def contract():
    import importlib

    return importlib.import_module("agentlens.diagnoser.executors.rule.custom.enhanced_contract")


def test_shared_enhanced_contract_available_without_importing_user_code():
    c = contract()
    assert c.RUNTIME_ID == "python-eval-v1"
    assert c.CAPABILITY == "python_eval_v1"
    code = c.validate_enhanced_code(
        "import nonexistent_library\nclass Helper: pass\ndef evaluate(trace, context): return {}"
    )
    assert code is not None


@pytest.mark.parametrize(
    "source",
    [
        "def evaluate(x): pass",
        "async def evaluate(trace): pass",
        "def evaluate(trace=None): pass",
        "def evaluate(trace,*args): pass",
        "@print\ndef evaluate(trace): pass",
        "def evaluate(trace): pass\ndef evaluate(trace): pass",
    ],
)
def test_invalid_enhanced_entry_rejected(source):
    with pytest.raises(ValueError):
        contract().validate_enhanced_code(source)


def test_materials_normalized_and_hashes_checked():
    c = contract()
    blob = b"\x00\xffxlsx"
    files = c.normalize_materials(
        {
            "files": [
                {
                    "path": "data/a.xlsx",
                    "encoding": "base64",
                    "content": base64.b64encode(blob).decode(),
                }
            ],
            "expected": {"total": 3},
        }
    )
    assert files["files"][0]["sha256"] == hashlib.sha256(blob).hexdigest()
    assert files["expected"] == {"total": 3}
    with pytest.raises(ValueError):
        c.normalize_materials({"files": [files["files"][0] | {"sha256": "0" * 64}]})


@pytest.mark.parametrize(
    "path", ["../a", "/a", "a/../b", "a//b", "./a", "a\\b", "https://a", "a.zip", "a\x00"]
)
def test_material_unsafe_path_rejected(path):
    with pytest.raises(ValueError):
        contract().normalize_materials(
            {"files": [{"path": path, "encoding": "utf-8", "content": "x"}]}
        )


def test_material_limits_duplicates_and_strict_fields():
    c = contract()
    f = {"path": "a", "encoding": "utf-8", "content": "x"}
    for value in [
        {"files": [f, f]},
        {"files": [f | {"content": "x" * (256 * 1024 + 1)}]},
        {"files": [f | {"path": str(i)} for i in range(21)]},
        {"files": [f | {"path": str(i), "content": "x" * (256 * 1024)} for i in range(3)]},
        {"files": [f | {"url": "https://a"}]},
    ]:
        with pytest.raises(ValueError):
            c.normalize_materials(value)


def test_manifest_digest_binds_packages():
    c = contract()
    manifest = {
        "id": c.RUNTIME_ID,
        "python_version": "3.11.14",
        "packages": {
            "pytest": "8.4.2",
            "numpy": "2.3.4",
            "pandas": "2.3.3",
            "pydantic": "2.12.3",
            "jsonschema": "4.25.1",
            "openpyxl": "3.1.5",
        },
    }
    digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    manifest["manifest_sha256"] = digest
    assert c.validate_runtime_manifest(manifest) == manifest
    with pytest.raises(ValueError):
        c.validate_runtime_manifest(
            manifest | {"packages": manifest["packages"] | {"numpy": "wrong"}}
        )


def test_expected_json_combined_with_files_shares_total_material_bound():
    value = {
        "files": [{"path": "a", "encoding": "utf-8", "content": "x" * (256 * 1024)}],
        "expected": "x" * (256 * 1024),
    }
    with pytest.raises(ValueError):
        contract().normalize_materials(value)


@pytest.mark.parametrize(
    "count,last,compact_total,accepted",
    [
        (131072, 1, 466945, True),
        (159743, 10, 512 * 1024, True),
        (159744, 1, 512 * 1024 + 1, False),
    ],
)
def test_expected_array_compact_budget_matches_frontend(count, last, compact_total, accepted):
    expected = [1] * count
    expected[-1] = last
    content = "x" * (200 * 1024)
    value = {
        "files": [{"path": "data.txt", "encoding": "utf-8", "content": content}],
        "expected": expected,
    }
    encoded = json.dumps(expected, separators=(",", ":"), ensure_ascii=False)
    assert len(content.encode()) + len(encoded.encode()) == compact_total
    if accepted:
        assert contract().normalize_materials(value)["expected"] == expected
    else:
        with pytest.raises(ValueError, match="bounded finite JSON"):
            contract().normalize_materials(value)


def test_expected_budget_counts_utf8_without_ascii_escapes():
    content = "x" * (200 * 1024)
    expected = {"text": "汉" * 100000}
    compact = json.dumps(expected, separators=(",", ":"), ensure_ascii=False).encode()
    assert len(content.encode()) + len(compact) < 512 * 1024
    normalized = contract().normalize_materials(
        {
            "files": [{"path": "data.txt", "encoding": "utf-8", "content": content}],
            "expected": expected,
        }
    )
    assert normalized["expected"] == expected


@pytest.mark.parametrize("expected", [float("nan"), float("inf"), float("-inf")])
def test_compact_expected_budget_still_rejects_nonfinite_json(expected):
    with pytest.raises(ValueError, match="bounded finite JSON"):
        contract().normalize_materials({"expected": expected})


def test_expected_json_preserves_javascript_exponent_wire_budget():
    # Actual JSON.stringify spelling; Python would expand each 1e-7 to 1e-07.
    wire = "[" + ",".join(["1e-7"] * 60000) + "]"
    content = "x" * (200 * 1024)
    assert len(content.encode()) + len(wire.encode()) == 504801
    envelope = json.loads(
        json.dumps(
            {
                "files": [{"path": "data.txt", "encoding": "utf-8", "content": content}],
                "expected_json": wire,
            }
        )
    )
    normalized = contract().normalize_materials(envelope)
    assert normalized["expected_json"] == wire
    assert "expected" not in normalized
    assert contract().normalize_materials(normalized) == normalized


@pytest.mark.parametrize("wire", [' {"汉": [1e-7, -0, 0, 1.0]} \n', '"before\u2028after"'])
def test_expected_json_preserves_original_unicode_spacing_and_number_bytes(wire):
    normalized = contract().normalize_materials({"expected_json": wire})
    assert normalized["expected_json"].encode() == wire.encode()


@pytest.mark.parametrize("extra", [0, 1])
def test_expected_json_raw_bytes_share_combined_limit(extra):
    content = "x" * (200 * 1024)
    wire = '"' + "x" * (512 * 1024 - len(content) - 2 + extra) + '"'
    value = {
        "files": [{"path": "data.txt", "encoding": "utf-8", "content": content}],
        "expected_json": wire,
    }
    assert len(wire.encode()) + len(content.encode()) == 512 * 1024 + extra
    if extra:
        with pytest.raises(ValueError):
            contract().normalize_materials(value)
    else:
        assert contract().normalize_materials(value)["expected_json"] == wire


@pytest.mark.parametrize(
    "value",
    [
        {"expected": None, "expected_json": "null"},
        {"expected": {}, "expected_json": "{}"},
    ],
)
def test_expected_json_conflicts_with_legacy_expected_even_when_null(value):
    with pytest.raises(ValueError, match="both"):
        contract().normalize_materials(value)


@pytest.mark.parametrize(
    "wire",
    ["", "{", "NaN", "Infinity", "-Infinity", "1e309", "[0,NaN]", '{"nested":Infinity}', 3, None],
)
def test_expected_json_rejects_invalid_or_nonfinite_wire(wire):
    with pytest.raises(ValueError):
        contract().normalize_materials({"expected_json": wire})


def test_expected_json_rejects_deep_containers_with_explicit_limit():
    c = contract()
    accepted = "[" * 64 + "0" + "]" * 64
    assert c.normalize_materials({"expected_json": accepted})["expected_json"] == accepted
    with pytest.raises(ValueError, match="depth"):
        c.normalize_materials({"expected_json": "[" + accepted + "]"})
