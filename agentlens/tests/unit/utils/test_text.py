"""smart_truncate —— 头尾保留 + 中间省略，替代盲头切（保住结尾结论）。"""

from agentlens.utils.text import smart_truncate


def test_short_text_unchanged():
    assert smart_truncate("hello", 100) == "hello"
    assert smart_truncate("x" * 100, 100) == "x" * 100  # 恰好等于预算不截


def test_non_str_passthrough():
    assert smart_truncate(None, 10) is None
    assert smart_truncate(123, 10) == 123


def test_head_and_tail_preserved_with_marker():
    text = "A" * 100 + "B" * 100  # 200 字
    out = smart_truncate(text, 100, head_ratio=0.6)
    assert out.startswith("A" * 60)  # 头 60
    assert out.endswith("B" * 40)  # 尾 40（盲头切会丢这段）
    assert "省略 100 字" in out  # 省略标记带字数
    assert len(out) < len(text)


def test_budget_zero_or_negative_safe():
    assert smart_truncate("abcdef", 0) == ""  # 预算 0 → 空（不崩）


def test_head_ratio_one_no_tail():
    out = smart_truncate("A" * 50 + "B" * 50, 20, head_ratio=1.0)
    assert out.startswith("A" * 20)
    assert "省略" in out
