from agentlens.web.routes.diagnoser import _managed_spec_signature


def test_multi_metric_managed_spec_signature_is_order_independent():
    left = {
        "executor": "llm_builtin",
        "metrics": [
            {"name": "urgent_triage"},
            {"name": "medical_safety"},
        ],
    }
    right = {
        "metrics": [
            {"name": "medical_safety"},
            {"name": "urgent_triage"},
        ],
        "executor": "llm_builtin",
    }

    assert _managed_spec_signature(left) == _managed_spec_signature(right)


def test_managed_spec_signature_tracks_sampling():
    base = {
        "executor": "total_latency",
        "sampling": {"ratio": 1},
        "metrics": [{"name": "latency_decomposition"}],
    }

    assert _managed_spec_signature(base) != _managed_spec_signature(
        {**base, "sampling": {"ratio": 0.5}}
    )


def test_managed_spec_signature_tracks_enable_thinking():
    spec = {
        "executor": "llm_builtin",
        "enable_thinking": False,
        "metrics": [{"name": "quality"}],
    }

    assert _managed_spec_signature(spec) != _managed_spec_signature(
        {**spec, "enable_thinking": True}
    )


def test_managed_spec_signature_tracks_temperature():
    spec = {
        "executor": "llm_builtin",
        "temperature": 0,
        "metrics": [{"name": "quality"}],
    }

    assert _managed_spec_signature(spec) != _managed_spec_signature({**spec, "temperature": 0.1})


def test_managed_spec_signature_tracks_image_vars():
    spec = {
        "executor": "llm_builtin",
        "metrics": [{"name": "quality"}],
    }

    assert _managed_spec_signature(spec) != _managed_spec_signature(
        {**spec, "image_vars": ["user_images"]}
    )
