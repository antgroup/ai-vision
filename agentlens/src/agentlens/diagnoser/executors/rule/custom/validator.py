"""Static AST validation for Custom Rule code."""

from __future__ import annotations

import ast
from typing import Any

MAX_CODE_BYTES = 10_240
MAX_LINES = 200
MAX_AST_NODES = 500
# The required metrics -> list -> metric object protocol adds two AST levels
# compared with the former flat return object.
MAX_NESTING_DEPTH = 12
MAX_STRING_LITERAL_LEN = 1000

# ── Whitelist: only these AST node types are allowed ──

_ALLOWED_AST_NODES: frozenset[type] = frozenset(
    {
        # Structure
        ast.Module,
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        # Statements
        ast.Return,
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        ast.For,
        ast.While,
        ast.If,
        ast.With,
        ast.Try,
        ast.ExceptHandler,
        # Expressions
        ast.BoolOp,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.Call,
        ast.Attribute,
        ast.Subscript,
        ast.Starred,
        ast.Name,
        ast.Constant,
        ast.JoinedStr,
        ast.FormattedValue,
        # Collections
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.Set,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.comprehension,
        # Simple statements
        ast.Expr,
        ast.Pass,
        ast.Break,
        ast.Continue,
        # Operators
        ast.And,
        ast.Or,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.Not,
        ast.USub,
        ast.UAdd,
        # Comparisons
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
        # Contexts & slicing
        ast.Load,
        ast.Store,
        ast.Del,
        ast.Slice,
        # Ternary & walrus
        ast.IfExp,
        ast.NamedExpr,
        # Allow Raise for user error handling
        ast.Raise,
    }
)

# ── Explicitly forbidden nodes (checked first for clear error messages) ──

_FORBIDDEN_AST_NODES_MAP: dict[type, str] = {
    ast.Import: "import 语句",
    ast.ImportFrom: "from ... import 语句",
    ast.ClassDef: "class 定义",
    ast.AsyncFunctionDef: "async 函数定义",
    ast.AsyncFor: "async for 语句",
    ast.AsyncWith: "async with 语句",
    ast.Lambda: "lambda 表达式",
    ast.Global: "global 声明",
    ast.Nonlocal: "nonlocal 声明",
    ast.Yield: "yield 表达式",
    ast.YieldFrom: "yield from 表达式",
    ast.Await: "await 表达式",
    ast.Delete: "delete 语句",
}

_FORBIDDEN_NAMES = frozenset(
    {
        "__import__",
        "exec",
        "eval",
        "compile",
        "open",
        "exit",
        "quit",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "__builtins__",
        "__loader__",
        "__spec__",
        "subprocess",
        "os",
        "sys",
        "shutil",
        "socket",
        "http",
        "urllib",
        "requests",
        "threading",
        "multiprocessing",
        "ctypes",
    }
)


class ValidationError(ValueError):
    def __init__(
        self,
        message: str,
        failure_code: str = "rule_validation_error",
    ) -> None:
        super().__init__(message)
        self.failure_code = failure_code


class SecurityViolation(ValidationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, failure_code="rule_security_violation")


def _count_nodes(tree: ast.AST) -> int:
    return sum(1 for _ in ast.walk(tree))


def _max_depth(node: ast.AST, depth: int = 0) -> int:
    max_d = depth
    for child in ast.iter_child_nodes(node):
        max_d = max(max_d, _max_depth(child, depth + 1))
    return max_d


def validate_code(code: str) -> Any:
    """Validate rule code and return a compiled code object.

    Raises ValidationError or SecurityViolation on failure.
    """
    if not isinstance(code, str) or not code.strip():
        raise ValidationError("code 不能为空")

    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise ValidationError(f"代码超过 {MAX_CODE_BYTES} 字节限制")

    lines = code.split("\n")
    if len(lines) > MAX_LINES:
        raise ValidationError(f"代码超过 {MAX_LINES} 行限制")

    try:
        tree = ast.parse(code, filename="<custom_rule>")
    except SyntaxError as exc:
        raise ValidationError(
            f"语法错误: {exc.msg} (行 {exc.lineno})",
        ) from exc

    node_count = _count_nodes(tree)
    if node_count > MAX_AST_NODES:
        raise ValidationError(
            f"AST 节点数 {node_count} 超过 {MAX_AST_NODES} 限制",
        )

    depth = _max_depth(tree)
    if depth > MAX_NESTING_DEPTH:
        raise ValidationError(
            f"嵌套深度 {depth} 超过 {MAX_NESTING_DEPTH} 限制",
        )

    # ── Function shape: exactly one top-level def evaluate(trace) ──

    func_defs = [node for node in ast.iter_child_nodes(tree) if isinstance(node, ast.FunctionDef)]
    if len(func_defs) != 1 or func_defs[0].name != "evaluate":
        raise ValidationError(
            "代码必须且只能包含一个顶层函数 def evaluate(trace):",
        )

    func = func_defs[0]
    args = func.args
    positional = [*args.posonlyargs, *args.args]
    if len(positional) != 1:
        raise ValidationError(
            "evaluate 函数必须接受且仅接受一个参数 (trace)",
        )
    if positional[0].arg != "trace":
        raise ValidationError("evaluate 函数的唯一参数必须命名为 trace")
    if args.defaults:
        raise ValidationError("evaluate 函数的 trace 参数不允许设置默认值")
    if args.vararg is not None:
        raise ValidationError("evaluate 函数不允许声明 *args")
    if args.kwonlyargs:
        raise ValidationError("evaluate 函数不允许声明 keyword-only 参数")
    if args.kwarg is not None:
        raise ValidationError("evaluate 函数不允许声明 **kwargs")
    if func.decorator_list:
        raise ValidationError("evaluate 函数不允许使用装饰器")

    # ── Only FunctionDef is allowed at top level (besides Module) ──

    for top_node in ast.iter_child_nodes(tree):
        if not isinstance(top_node, (ast.FunctionDef, ast.Expr)):
            # ast.Expr covers docstrings at module level
            if isinstance(top_node, ast.Expr):
                continue
            node_type = type(top_node).__name__
            lineno = getattr(top_node, "lineno", "?")
            if not isinstance(top_node, ast.FunctionDef):
                raise ValidationError(
                    f"顶层只允许函数定义，发现 {node_type} (行 {lineno})",
                )

    # ── Walk all nodes: check forbidden first, then whitelist ──

    for node in ast.walk(tree):
        node_cls = type(node)
        lineno = getattr(node, "lineno", "?")

        if node_cls in _FORBIDDEN_AST_NODES_MAP:
            desc = _FORBIDDEN_AST_NODES_MAP[node_cls]
            raise SecurityViolation(
                f"禁止使用 {desc} ({node_cls.__name__}，行 {lineno})",
            )

        if node_cls not in _ALLOWED_AST_NODES:
            raise SecurityViolation(
                f"不允许的语法节点 {node_cls.__name__} (行 {lineno})",
            )

        # Forbidden built-in names
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise SecurityViolation(
                f"禁止访问 {node.id!r} (行 {node.lineno})",
            )

        # Dunder attribute access
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SecurityViolation(
                f"禁止访问 dunder 属性 {node.attr!r} (行 {node.lineno})",
            )

        # String literal length
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) > MAX_STRING_LITERAL_LEN:
                raise ValidationError(
                    f"字符串字面量超过 {MAX_STRING_LITERAL_LEN} 字符限制 (行 {node.lineno})",
                )

    try:
        code = compile(tree, "<custom_rule>", "exec")
    except Exception as exc:
        raise ValidationError(f"编译失败: {exc}") from exc

    return code
