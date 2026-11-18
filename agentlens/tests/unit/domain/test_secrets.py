from agentlens.domain.secrets import merge_secret_fields, redact_spec


def test_redact_spec_removes_diagnoser_model_api_key():
    spec = {"model": {"endpoint": "http://x", "model_id": "m", "api_key": "sk-real"}}
    out = redact_spec(spec)
    assert "api_key" not in out["model"]
    assert out["model"]["api_key_configured"] is True
    assert out["model"]["endpoint"] == "http://x"
    assert out["model"]["model_id"] == "m"


def test_redact_spec_marks_unconfigured_when_absent():
    spec = {"model": {"endpoint": "http://x"}}
    out = redact_spec(spec)
    assert out["model"]["api_key_configured"] is False


def test_redact_spec_does_not_mutate_input():
    spec = {"model": {"api_key": "sk-real"}}
    redact_spec(spec)
    assert spec["model"]["api_key"] == "sk-real"


def test_merge_preserves_secret_when_omitted():
    old = {"model": {"endpoint": "http://old", "api_key": "sk-old", "model_id": "m1"}}
    new = {"model": {"endpoint": "http://new", "model_id": "m1"}}
    merged = merge_secret_fields(old, new)
    assert merged["model"]["api_key"] == "sk-old"
    assert merged["model"]["endpoint"] == "http://new"


def test_merge_rotates_secret_when_explicitly_provided():
    old = {"model": {"api_key": "sk-old"}}
    new = {"model": {"api_key": "sk-new-rotated"}}
    merged = merge_secret_fields(old, new)
    assert merged["model"]["api_key"] == "sk-new-rotated"


def test_merge_clears_secret_on_explicit_empty_string():
    old = {"model": {"api_key": "sk-old", "endpoint": "http://x"}}
    new = {"model": {"api_key": "", "endpoint": "http://x"}}
    merged = merge_secret_fields(old, new)
    assert "api_key" not in merged["model"]
    assert merged["model"]["endpoint"] == "http://x"


def test_merge_with_no_old_spec_keeps_new_value_only():
    merged = merge_secret_fields(None, {"model": {"api_key": "sk-fresh"}})
    assert merged["model"]["api_key"] == "sk-fresh"


def test_merge_does_not_mutate_inputs():
    old = {"model": {"api_key": "sk-old"}}
    new = {"model": {"model_id": "m"}}
    merge_secret_fields(old, new)
    assert old == {"model": {"api_key": "sk-old"}}
    assert new == {"model": {"model_id": "m"}}
