import pytest

from agentlens.diagnoser.executors.rule.custom.validator import (
    SecurityViolation,
    ValidationError,
    validate_code,
)

VALID_CODE = """
def evaluate(trace):
    output = trace.get("agent_output", "")
    if not output:
        return {"metrics": [
            {"name": "quality", "verdict": "bad", "reason": "no output", "evidence": {}}
        ]}
    return {"metrics": [
        {"name": "quality", "verdict": "good", "reason": "has output", "evidence": {}}
    ]}
"""

VALID_NOOP = """
def evaluate(trace):
    return {"metrics": [
        {"name": "quality", "verdict": "good", "reason": "not applicable", "evidence": {}}
    ]}
"""


class TestValidCode:
    def test_valid_function_compiles(self):
        code = validate_code(VALID_CODE)
        assert code is not None

    def test_valid_noop_function(self):
        code = validate_code(VALID_NOOP)
        assert code is not None

    def test_json_re_math_allowed(self):
        code_text = """
def evaluate(trace):
    import_free = json.dumps({"a": 1})
    pattern = re.compile(r"\\d+")
    val = math.sqrt(4)
    return {"metrics": [{"name": "quality", "verdict": "good", "reason": "ok", "evidence": {}}]}
"""
        code = validate_code(code_text)
        assert code is not None


class TestSyntaxErrors:
    def test_syntax_error_rejected(self):
        with pytest.raises(ValidationError, match="语法错误"):
            validate_code("def evaluate(trace)\n  pass")

    def test_empty_code_rejected(self):
        with pytest.raises(ValidationError, match="不能为空"):
            validate_code("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValidationError, match="不能为空"):
            validate_code("   \n  \n  ")


class TestFunctionShape:
    def test_no_function_rejected(self):
        with pytest.raises(ValidationError, match="evaluate"):
            validate_code("x = 1")

    def test_wrong_function_name_rejected(self):
        with pytest.raises(ValidationError, match="evaluate"):
            validate_code("def check(trace):\n  pass")

    def test_multiple_functions_rejected(self):
        with pytest.raises(ValidationError, match="只能包含一个"):
            validate_code("def evaluate(trace):\n  pass\ndef helper():\n  pass")

    def test_no_params_rejected(self):
        with pytest.raises(ValidationError, match="一个参数"):
            validate_code("def evaluate():\n  pass")

    def test_multiple_params_rejected(self):
        with pytest.raises(ValidationError, match="一个参数"):
            validate_code("def evaluate(trace, extra):\n  pass")

    def test_parameter_must_be_named_trace(self):
        with pytest.raises(ValidationError, match="命名为 trace"):
            validate_code("def evaluate(value):\n  pass")

    def test_default_value_rejected(self):
        with pytest.raises(ValidationError, match="默认值"):
            validate_code("def evaluate(trace=None):\n  pass")

    def test_varargs_rejected(self):
        with pytest.raises(ValidationError, match=r"\*args"):
            validate_code("def evaluate(trace, *args):\n  pass")

    def test_keyword_only_parameter_rejected(self):
        with pytest.raises(ValidationError, match="keyword-only"):
            validate_code("def evaluate(trace, *, required):\n  pass")

    def test_kwargs_rejected(self):
        with pytest.raises(ValidationError, match=r"\*\*kwargs"):
            validate_code("def evaluate(trace, **kwargs):\n  pass")

    def test_decorator_rejected(self):
        with pytest.raises(ValidationError, match="装饰器"):
            validate_code("@staticmethod\ndef evaluate(trace):\n  pass")


class TestSecurityViolations:
    def test_import_rejected(self):
        with pytest.raises((SecurityViolation, ValidationError)):
            validate_code("import os\ndef evaluate(trace):\n  pass")

    def test_from_import_rejected(self):
        with pytest.raises((SecurityViolation, ValidationError)):
            validate_code("from os import path\ndef evaluate(trace):\n  pass")

    def test_class_rejected(self):
        with pytest.raises((SecurityViolation, ValidationError)):
            validate_code("class Foo:\n  pass\ndef evaluate(trace):\n  pass")

    def test_async_rejected(self):
        with pytest.raises((SecurityViolation, ValidationError)):
            validate_code("async def evaluate(trace):\n  pass")

    def test_exec_name_rejected(self):
        with pytest.raises(SecurityViolation, match="exec"):
            validate_code("def evaluate(trace):\n  exec('pass')")

    def test_eval_name_rejected(self):
        with pytest.raises(SecurityViolation, match="eval"):
            validate_code("def evaluate(trace):\n  eval('1+1')")

    def test_open_name_rejected(self):
        with pytest.raises(SecurityViolation, match="open"):
            validate_code("def evaluate(trace):\n  open('/etc/passwd')")

    def test_dunder_attribute_rejected(self):
        with pytest.raises(SecurityViolation, match="dunder"):
            validate_code("def evaluate(trace):\n  x = trace.__class__")

    def test_globals_name_rejected(self):
        with pytest.raises(SecurityViolation, match="globals"):
            validate_code("def evaluate(trace):\n  globals()")

    def test_getattr_rejected(self):
        with pytest.raises(SecurityViolation, match="getattr"):
            validate_code("def evaluate(trace):\n  getattr(trace, 'x')")


class TestSizeLimits:
    def test_too_many_lines_rejected(self):
        code = "def evaluate(trace):\n" + "  x = 1\n" * 201
        with pytest.raises(ValidationError, match="行限制"):
            validate_code(code)

    def test_long_string_literal_rejected(self):
        code = f'def evaluate(trace):\n  x = "{"a" * 1001}"'
        with pytest.raises(ValidationError, match="字符串字面量"):
            validate_code(code)
