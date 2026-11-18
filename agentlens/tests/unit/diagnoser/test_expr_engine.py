"""Expression engine 单测：parse / resolve / serialize / preprocess / render / query。"""

import json

import pytest

from agentlens.service.datasource.projection.expressions import (
    _flatten,
    _get_nested,
    _is_array_op,
    parse,
    preprocess,
    query,
    render,
    resolve,
    serialize,
)

# ── Test data ────────────────────────────────────────────────────────

TRACE = {
    "trace_id": "abc123",
    "session_id": "sess-1",
    "spans": [
        {
            "span_id": "s1",
            "parent_span_id": "",
            "name": "root-agent",
            "kind": "AGENT",
            "attributes": {
                "gen_ai.span.kind": "AGENT",
                "gen_ai.request.model": "gpt-4",
                "gen_ai.input.messages": json.dumps(
                    [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Hello"},
                    ]
                ),
                "gen_ai.output.messages": json.dumps(
                    [
                        {"role": "assistant", "content": "Hi there!"},
                    ]
                ),
                "gen_ai.usage.input_tokens": "100",
                "gen_ai.usage.output_tokens": "20",
            },
            "resource": {"service.name": "my-agent"},
            "status": {"code": "OK", "message": ""},
        },
        {
            "span_id": "s2",
            "parent_span_id": "s1",
            "name": "llm-call",
            "kind": "LLM",
            "attributes": {
                "gen_ai.span.kind": "LLM",
                "gen_ai.request.model": "gpt-4",
                "gen_ai.input.messages": json.dumps(
                    [
                        {"role": "user", "content": "What is 2+2?"},
                    ]
                ),
                "gen_ai.output.messages": json.dumps(
                    [
                        {"role": "assistant", "content": "4"},
                    ]
                ),
                "gen_ai.usage.input_tokens": "50",
                "gen_ai.usage.output_tokens": "5",
            },
            "resource": {"service.name": "my-agent"},
            "status": {"code": "OK", "message": ""},
        },
        {
            "span_id": "s3",
            "parent_span_id": "s1",
            "name": "tool-search",
            "kind": "TOOL",
            "attributes": {
                "gen_ai.span.kind": "TOOL",
                "gen_ai.tool.name": "searchTool",
                "gen_ai.tool.call.arguments": json.dumps({"query": "weather"}),
                "gen_ai.tool.call.result": "sunny, 25C",
            },
            "resource": {"service.name": "my-agent"},
            "status": {"code": "OK", "message": ""},
        },
        {
            "span_id": "s4",
            "parent_span_id": "s2",
            "name": "llm-retry",
            "kind": "LLM",
            "attributes": {
                "gen_ai.span.kind": "LLM",
                "gen_ai.request.model": "gpt-3.5-turbo",
                "gen_ai.input.messages": json.dumps(
                    [
                        {"role": "user", "content": "Retry question"},
                    ]
                ),
                "cancelled": "true",
            },
            "resource": {"service.name": "my-agent"},
            "status": {"code": "UNSET", "message": "cancelled"},
        },
    ],
    "tree": [],
}


@pytest.fixture()
def flat_data():
    return preprocess(TRACE)


@pytest.fixture()
def tree_data():
    return preprocess(TRACE, build_tree=True)


# ── parse ────────────────────────────────────────────────────────────


class TestParse:
    def test_simple_key(self):
        assert parse("name") == [{"key": "name"}]

    def test_dotted_path(self):
        assert parse("a.b.c") == [{"key": "a"}, {"key": "b"}, {"key": "c"}]

    def test_wildcard(self):
        assert parse("spans[*].name") == [{"key": "spans"}, {"wildcard": True}, {"key": "name"}]

    def test_index(self):
        assert parse("spans[0].name") == [{"key": "spans"}, {"index": 0}, {"key": "name"}]

    def test_negative_index(self):
        assert parse("spans[-1].name") == [{"key": "spans"}, {"index": -1}, {"key": "name"}]

    def test_filter(self):
        segs = parse("spans[kind:LLM].name")
        assert segs == [{"key": "spans"}, {"filter": ("kind", "LLM")}, {"key": "name"}]

    def test_filter_with_dots_in_key(self):
        segs = parse("spans[attributes.gen_ai.span.kind:LLM]")
        assert segs[1] == {"filter": ("attributes.gen_ai.span.kind", "LLM")}

    def test_filter_with_colon_in_value(self):
        segs = parse("spans[name:LLM progress:INTENTION]")
        assert segs[1] == {"filter": ("name", "LLM progress:INTENTION")}

    def test_inclusive_range(self):
        segs = parse("spans[1,3].name")
        assert segs[1] == {"range": (1, 3)}

    def test_python_slice(self):
        assert parse("spans[1:3]")[1] == {"slice": (1, 3)}

    def test_slice_open_end(self):
        assert parse("spans[2:]")[1] == {"slice": (2, None)}

    def test_slice_open_start(self):
        assert parse("spans[:3]")[1] == {"slice": (None, 3)}

    def test_slice_negative(self):
        assert parse("spans[-2:]")[1] == {"slice": (-2, None)}
        assert parse("spans[:-1]")[1] == {"slice": (None, -1)}

    def test_complex_path(self):
        segs = parse("spans[attributes.gen_ai.span.kind:LLM].attributes.gen_ai.request.model")
        assert len(segs) == 6
        assert segs[0] == {"key": "spans"}
        assert segs[1] == {"filter": ("attributes.gen_ai.span.kind", "LLM")}
        assert segs[2] == {"key": "attributes"}
        # remaining segments are individual dot-separated keys; greedy match at resolve time
        assert segs[3] == {"key": "gen_ai"}
        assert segs[4] == {"key": "request"}
        assert segs[5] == {"key": "model"}


# ── _get_nested ──────────────────────────────────────────────────────


class TestGetNested:
    def test_exact_match(self):
        d = {"gen_ai.span.kind": "LLM"}
        assert _get_nested(d, "gen_ai.span.kind") == "LLM"

    def test_nested_dict(self):
        d = {"a": {"b": {"c": 42}}}
        assert _get_nested(d, "a.b.c") == 42

    def test_greedy_match(self):
        d = {"attributes": {"gen_ai.span.kind": "LLM"}}
        assert _get_nested(d, "attributes.gen_ai.span.kind") == "LLM"

    def test_flat_prefix_is_not_inferred_as_nested_object(self):
        d = {
            "attributes.gen_ai.span.kind": "LLM",
            "attributes.gen_ai.request.model": "gpt-4",
        }
        assert _get_nested(d, "attributes") is None

    def test_longest_real_flat_key_wins(self):
        d = {"a.b": "parent", "a.b.c": "child"}
        assert _get_nested(d, "a.b") == "parent"
        assert _get_nested(d, "a.b.c") == "child"

    def test_missing_returns_none(self):
        assert _get_nested({"a": 1}, "b") is None

    def test_non_dict_returns_none(self):
        assert _get_nested("string", "a") is None
        assert _get_nested(None, "a") is None


# ── _is_array_op ─────────────────────────────────────────────────────


class TestIsArrayOp:
    def test_index(self):
        assert _is_array_op({"index": 0})

    def test_wildcard(self):
        assert _is_array_op({"wildcard": True})

    def test_slice(self):
        assert _is_array_op({"slice": (0, 3)})

    def test_range(self):
        assert _is_array_op({"range": (1, 3)})

    def test_key_is_not(self):
        assert not _is_array_op({"key": "name"})

    def test_filter_is_not(self):
        assert not _is_array_op({"filter": ("k", "v")})


# ── resolve: index ───────────────────────────────────────────────────


class TestResolveIndex:
    def test_first(self, flat_data):
        assert resolve(flat_data, parse("spans[0].name")) == "root-agent"

    def test_second(self, flat_data):
        assert resolve(flat_data, parse("spans[1].name")) == "llm-call"

    def test_last(self, flat_data):
        assert resolve(flat_data, parse("spans[-1].name")) == "llm-retry"

    def test_second_to_last(self, flat_data):
        assert resolve(flat_data, parse("spans[-2].name")) == "tool-search"

    def test_out_of_bounds(self, flat_data):
        assert resolve(flat_data, parse("spans[99].name")) is None

    def test_negative_out_of_bounds(self, flat_data):
        assert resolve(flat_data, parse("spans[-99].name")) is None


# ── resolve: wildcard ────────────────────────────────────────────────


class TestResolveWildcard:
    def test_all_names(self, flat_data):
        result = resolve(flat_data, parse("spans[*].name"))
        assert result == ["root-agent", "llm-call", "tool-search", "llm-retry"]

    def test_all_kinds(self, flat_data):
        result = resolve(flat_data, parse("spans[*].kind"))
        assert result == ["AGENT", "LLM", "TOOL", "LLM"]

    def test_empty_array(self):
        data = {"items": []}
        assert resolve(data, parse("items[*].x")) == []


# ── resolve: filter ──────────────────────────────────────────────────


class TestResolveFilter:
    def test_filter_by_kind(self, flat_data):
        result = resolve(flat_data, parse("spans[kind:LLM].name"))
        assert result == ["llm-call", "llm-retry"]

    def test_filter_by_nested_attr(self, flat_data):
        result = resolve(
            flat_data, parse("spans[attributes.gen_ai.span.kind:TOOL].attributes.gen_ai.tool.name")
        )
        assert result == ["searchTool"]

    def test_filter_no_match(self, flat_data):
        result = resolve(flat_data, parse("spans[kind:NONEXISTENT].name"))
        assert result == []

    def test_filter_then_index(self, flat_data):
        result = resolve(flat_data, parse("spans[kind:LLM][-1].name"))
        assert result == "llm-retry"

    def test_filter_then_wildcard(self, flat_data):
        result = resolve(flat_data, parse("spans[kind:LLM][*].name"))
        assert result == ["llm-call", "llm-retry"]

    def test_filter_then_slice(self, flat_data):
        result = resolve(flat_data, parse("spans[kind:LLM][0:1].name"))
        assert result == ["llm-call"]


# ── resolve: range and slice ─────────────────────────────────────────


class TestResolveRangeSlice:
    def test_inclusive_range(self, flat_data):
        result = resolve(flat_data, parse("spans[0,2].name"))
        assert result == ["root-agent", "llm-call", "tool-search"]

    def test_python_slice(self, flat_data):
        result = resolve(flat_data, parse("spans[0:2].name"))
        assert result == ["root-agent", "llm-call"]

    def test_slice_open_end(self, flat_data):
        result = resolve(flat_data, parse("spans[2:].name"))
        assert result == ["tool-search", "llm-retry"]

    def test_slice_open_start(self, flat_data):
        result = resolve(flat_data, parse("spans[:2].name"))
        assert result == ["root-agent", "llm-call"]

    def test_slice_negative_end(self, flat_data):
        result = resolve(flat_data, parse("spans[:-1].name"))
        assert result == ["root-agent", "llm-call", "tool-search"]

    def test_slice_negative_start(self, flat_data):
        result = resolve(flat_data, parse("spans[-2:].name"))
        assert result == ["tool-search", "llm-retry"]


# ── resolve: greedy key match (OTel dotted keys) ─────────────────────


class TestResolveGreedyKey:
    def test_dotted_attribute_key(self, flat_data):
        result = resolve(flat_data, parse("spans[0].attributes.gen_ai.request.model"))
        assert result == "gpt-4"

    def test_dotted_attribute_filter_and_access(self, flat_data):
        result = resolve(
            flat_data,
            parse("spans[attributes.gen_ai.span.kind:LLM].attributes.gen_ai.request.model"),
        )
        assert result == ["gpt-4", "gpt-3.5-turbo"]

    def test_resource_dotted_key(self, flat_data):
        result = resolve(flat_data, parse("spans[0].resource.service.name"))
        assert result == "my-agent"

    def test_flat_attribute_group_is_not_inferred(self):
        data = preprocess(
            {
                "spans": [
                    {
                        "attributes.gen_ai.span.kind": "LLM",
                        "attributes.gen_ai.request.model": "gpt-4",
                    }
                ]
            }
        )
        assert resolve(data, parse("spans[0].attributes")) is None

    def test_exact_flat_path_ignores_unrelated_prefix_conflicts(self):
        data = preprocess(
            {
                "spans": [
                    {
                        "attributes.mmflow.dag.node": None,
                        "attributes.mmflow.dag.node.class": None,
                        "attributes.error.type": None,
                    },
                    {
                        "attributes.mmflow.dag.node.class": "ImageNode",
                        "attributes.mmflow.dag.node": "generate",
                        "attributes.error.type": "10007",
                    },
                ]
            }
        )

        assert resolve(data, parse("spans[*].attributes.error.type")) == ["10007"]

    @pytest.mark.parametrize(
        "span",
        [
            {
                "attributes.mmflow.dag.node": None,
                "attributes.mmflow.dag.node.class": None,
            },
            {
                "attributes.mmflow.dag.node.class": None,
                "attributes.mmflow.dag.node": None,
            },
        ],
    )
    def test_flat_parent_and_child_keys_are_independently_readable(self, span):
        data = preprocess({"spans": [span]})

        assert resolve(data, parse("spans[0].attributes.mmflow.dag.node")) is None
        assert resolve(data, parse("spans[0].attributes.mmflow.dag.node.class")) is None
        assert resolve(data, parse("spans[0].attributes")) is None

    def test_flat_parent_and_child_keep_distinct_values(self):
        data = preprocess(
            {
                "spans": [
                    {
                        "attributes.mmflow.dag.node": "parent",
                        "attributes.mmflow.dag.node.class": "ImageNode",
                    }
                ]
            }
        )

        assert resolve(data, parse("spans[0].attributes.mmflow.dag.node")) == "parent"
        assert resolve(data, parse("spans[0].attributes.mmflow.dag.node.class")) == "ImageNode"

    def test_real_nested_object_keeps_its_structure(self):
        data = preprocess({"spans": [{"attributes": {"error": {"type": "NestedError"}}}]})

        assert resolve(data, parse("spans[0].attributes")) == {"error": {"type": "NestedError"}}
        assert resolve(data, parse("spans[0].attributes.error.type")) == "NestedError"


# ── resolve: implicit fan-out ────────────────────────────────────────


class TestResolveImplicitFanOut:
    def test_messages_content_implicit(self, flat_data):
        """Implicit fan-out: messages.content == messages[*].content (Datadog-compatible)."""
        result = resolve(flat_data, parse("spans[0].attributes.gen_ai.input.messages.content"))
        assert result == ["You are a helpful assistant.", "Hello"]

    def test_messages_role_implicit(self, flat_data):
        result = resolve(flat_data, parse("spans[0].attributes.gen_ai.input.messages.role"))
        assert result == ["system", "user"]

    def test_explicit_wildcard_same_result(self, flat_data):
        """Explicit [*] produces same result as implicit fan-out."""
        implicit = resolve(flat_data, parse("spans[0].attributes.gen_ai.input.messages.content"))
        explicit = resolve(flat_data, parse("spans[0].attributes.gen_ai.input.messages[*].content"))
        assert implicit == explicit

    def test_plain_array_implicit_fanout(self):
        """Implicit fan-out works on plain nested structures."""
        data = {"items": [{"x": 1}, {"x": 2}]}
        result = resolve(data, parse("items.x"))
        assert result == [1, 2]


# ── resolve: JSON auto-parse in preprocess ───────────────────────────


class TestJsonAutoParse:
    def test_messages_parsed(self, flat_data):
        msgs = resolve(flat_data, parse("spans[0].attributes.gen_ai.input.messages"))
        assert isinstance(msgs, list)
        assert msgs[0]["role"] == "system"

    def test_tool_args_parsed(self, flat_data):
        args = resolve(
            flat_data,
            parse("spans[attributes.gen_ai.span.kind:TOOL].attributes.gen_ai.tool.call.arguments"),
        )
        assert args == [{"query": "weather"}]

    def test_tool_result_string_not_parsed(self, flat_data):
        """Plain strings that don't start with [ or { should not be parsed."""
        result = resolve(
            flat_data,
            parse("spans[attributes.gen_ai.span.kind:TOOL].attributes.gen_ai.tool.call.result"),
        )
        assert result == ["sunny, 25C"]

    def test_filter_into_parsed_messages(self, flat_data):
        result = resolve(
            flat_data, parse("spans[1].attributes.gen_ai.input.messages[role:user].content")
        )
        assert result == ["What is 2+2?"]


# ── resolve: missing paths ───────────────────────────────────────────


class TestResolveMissing:
    def test_missing_key(self, flat_data):
        assert resolve(flat_data, parse("nonexistent")) is None

    def test_missing_nested(self, flat_data):
        assert resolve(flat_data, parse("spans[0].nonexistent.deep.path")) is None

    def test_none_data(self):
        assert resolve(None, parse("a.b")) is None

    def test_empty_segments(self, flat_data):
        assert resolve(flat_data, []) == flat_data


# ── preprocess: build_tree flag ──────────────────────────────────────


class TestPreprocessBuildTree:
    def test_default_no_children(self):
        data = preprocess(TRACE)
        assert "children" not in data["spans"][0]

    def test_build_tree_adds_children(self):
        data = preprocess(TRACE, build_tree=True)
        root = data["spans"][0]
        assert "children" in root
        child_names = [c["name"] for c in root["children"]]
        assert "llm-call" in child_names
        assert "tool-search" in child_names

    def test_tree_navigation(self):
        data = preprocess(TRACE, build_tree=True)
        result = resolve(data, parse("spans[name:root-agent].children[*].name"))
        assert "llm-call" in result
        assert "tool-search" in result

    def test_deep_tree_navigation(self):
        data = preprocess(TRACE, build_tree=True)
        result = resolve(
            data, parse("spans[name:root-agent].children[name:llm-call].children[*].name")
        )
        assert result == ["llm-retry"]

    def test_tree_with_filter(self):
        data = preprocess(TRACE, build_tree=True)
        result = resolve(
            data,
            parse(
                "spans[name:root-agent].children[attributes.gen_ai.span.kind:TOOL].attributes.gen_ai.tool.name"
            ),
        )
        assert result == ["searchTool"]


# ── _flatten ─────────────────────────────────────────────────────────


class TestFlatten:
    def test_nested_lists(self):
        assert _flatten([[1, 2], [3]]) == [1, 2, 3]

    def test_mixed(self):
        assert _flatten([1, [2, 3], 4]) == [1, 2, 3, 4]

    def test_none_stripped(self):
        assert _flatten([1, None, 2]) == [1, 2]

    def test_empty(self):
        assert _flatten([]) == []


# ── serialize ────────────────────────────────────────────────────────


class TestSerialize:
    def test_none(self):
        assert serialize(None) == ""

    def test_string(self):
        assert serialize("hello") == "hello"

    def test_string_list_joined(self):
        assert serialize(["a", "b", "c"]) == "a\nb\nc"

    def test_empty_list(self):
        assert serialize([]) == ""

    def test_dict_json(self):
        result = serialize({"key": "val"})
        assert json.loads(result) == {"key": "val"}

    def test_int(self):
        assert serialize(42) == "42"

    def test_bool(self):
        assert serialize(True) == "true"

    def test_mixed_list_json(self):
        result = serialize(["a", {"b": 1}])
        parsed = json.loads(result)
        assert parsed == ["a", {"b": 1}]

    def test_object_list_json(self):
        result = serialize([{"a": 1}, {"b": 2}])
        parsed = json.loads(result)
        assert len(parsed) == 2


# ── query (convenience wrapper) ──────────────────────────────────────


class TestQuery:
    def test_star(self, flat_data):
        assert query("*", flat_data) == flat_data

    def test_simple(self, flat_data):
        assert query("trace_id", flat_data) == "abc123"

    def test_expression(self, flat_data):
        result = query("spans[*].name", flat_data)
        assert len(result) == 4

    def test_whitespace_stripped(self, flat_data):
        assert query("  trace_id  ", flat_data) == "abc123"


# ── render (template engine) ─────────────────────────────────────────


class TestRender:
    def test_simple_var(self, flat_data):
        assert render("id={{trace_id}}", flat_data) == "id=abc123"

    def test_expression_var(self, flat_data):
        result = render("names={{spans[*].name}}", flat_data)
        assert "root-agent" in result
        assert "llm-call" in result

    def test_star(self, flat_data):
        result = render("all={{*}}", flat_data)
        assert "abc123" in result

    def test_multiple_vars(self, flat_data):
        result = render("{{trace_id}}-{{session_id}}", flat_data)
        assert result == "abc123-sess-1"

    def test_missing_var(self, flat_data):
        assert render("x={{nope}}", flat_data) == "x="

    def test_no_vars(self, flat_data):
        assert render("plain text", flat_data) == "plain text"

    def test_mixed_simple_and_expression(self, flat_data):
        tpl = "trace={{trace_id}} model={{spans[0].attributes.gen_ai.request.model}}"
        result = render(tpl, flat_data)
        assert result == "trace=abc123 model=gpt-4"


# ── End-to-end scenarios ─────────────────────────────────────────────


class TestEndToEnd:
    def test_llm_tool_sequence(self, flat_data):
        """Extract the LLM→Tool call sequence."""
        models = query(
            "spans[attributes.gen_ai.span.kind:LLM].attributes.gen_ai.request.model",
            flat_data,
        )
        tools = query(
            "spans[attributes.gen_ai.span.kind:TOOL].attributes.gen_ai.tool.name",
            flat_data,
        )
        assert models == ["gpt-4", "gpt-3.5-turbo"]
        assert tools == ["searchTool"]

    def test_user_messages_across_spans(self, flat_data):
        """Get all user messages from all LLM spans."""
        result = query(
            "spans[attributes.gen_ai.span.kind:LLM].attributes.gen_ai.input.messages[role:user].content",
            flat_data,
        )
        assert "What is 2+2?" in result
        assert "Retry question" in result

    def test_last_llm_model(self, flat_data):
        """Get the model of the last LLM call."""
        result = query(
            "spans[attributes.gen_ai.span.kind:LLM][-1].attributes.gen_ai.request.model",
            flat_data,
        )
        assert result == "gpt-3.5-turbo"

    def test_token_counts(self, flat_data):
        result = query(
            "spans[attributes.gen_ai.span.kind:LLM].attributes.gen_ai.usage.input_tokens",
            flat_data,
        )
        assert "50" in result

    def test_cancelled_span(self, flat_data):
        result = query("spans[-1].attributes.cancelled", flat_data)
        assert result == "true"

    def test_prompt_template(self, flat_data):
        template = """请评估以下对话：

用户输入：
{{spans[attributes.gen_ai.span.kind:LLM][0].attributes.gen_ai.input.messages[role:user].content}}

模型输出：
{{spans[attributes.gen_ai.span.kind:LLM][0].attributes.gen_ai.output.messages[role:assistant].content}}

使用模型：{{spans[attributes.gen_ai.span.kind:LLM][0].attributes.gen_ai.request.model}}
"""
        rendered = render(template, flat_data)
        assert "What is 2+2?" in rendered
        assert "4" in rendered
        assert "gpt-4" in rendered
