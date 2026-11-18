import json

import pytest

from agentlens.service.datasource.projection.projectors.coding.adapter import (
    derive_verification_actions,
)
from agentlens.service.datasource.projection.projectors.coding.models import (
    ToolAction as CodingToolAction,
)
from agentlens.service.datasource.projection.projectors.coding.tool_actions import (
    extract_tool_actions,
)
from tests.unit.diagnoser.projection_factory import projection_from_dict


def _trace(*steps):
    return projection_from_dict(
        {
            "trace_id": "t",
            "runtime_type": "cc",
            "user_query": "q",
            "agent_output": "a",
            "outcome": {
                "has_error": False,
                "result_state": "SUCCESS",
                "total_latency_ms": 1000,
                "interrupted": False,
            },
            "steps": list(steps),
        }
    )


def _step(name, input_value, *, output=None, status="OK"):
    return {
        "kind": "execution",
        "name": name,
        "type": "TOOL",
        "status": status,
        "input": input_value,
        "output": output,
    }


def test_extract_tool_actions_normalizes_opencode_camel_case_edit():
    trace = _trace(
        _step(
            "tool edit",
            json.dumps(
                {
                    "filePath": "scripts/check.sh",
                    "oldString": "echo old",
                    "newString": "echo new",
                }
            ),
        )
    )

    action = extract_tool_actions(trace)[0]

    assert action.operations == frozenset({"edit"})
    assert action.paths == ("scripts/check.sh",)
    assert action.old_content == "echo old"
    assert action.new_content == "echo new"
    assert action.succeeded is True


def test_extract_tool_actions_normalizes_antigravity_write_and_command():
    trace = _trace(
        _step(
            "tool write_to_file",
            json.dumps({"TargetFile": "src/app.py", "CodeContent": "print('ok')"}),
        ),
        _step(
            "tool run_command",
            json.dumps({"CommandLine": "pytest -q", "Cwd": "/repo"}),
            output=json.dumps({"exit_code": 0}),
        ),
    )

    write, test = extract_tool_actions(trace)

    assert write.operations == frozenset({"write"})
    assert write.paths == ("src/app.py",)
    assert test.operations == frozenset({"shell", "test"})
    assert test.command == "pytest -q"
    assert test.succeeded is True


def test_extract_tool_actions_prefers_nonzero_exit_code_over_ok_span():
    trace = _trace(
        _step(
            "tool Bash",
            json.dumps({"command": "pytest -q"}),
            output=json.dumps({"exit_code": 2, "stderr": "failed"}),
            status="OK",
        )
    )

    action = extract_tool_actions(trace)[0]

    assert action.succeeded is False


@pytest.mark.parametrize(
    ("output", "status", "succeeded", "verification"),
    [
        ("[stdout]\nRan 2 tests in 0.001s\nOK\n[exit code: 0]", "success", True, "passed"),
        (
            "[stderr]\ntest_add ... FAIL\ntest_negative ... FAIL\n"
            "Ran 2 tests in 0.001s\nFAILED (failures=2)\n[exit code: 1]",
            "success",
            False,
            "failed",
        ),
        ("[stderr]\n[exit code: 1]", "unknown", False, "failed"),
        ("[stdout]\n[exit code: 0]\n", "error", True, "passed"),
    ],
)
def test_dsh_shell_exit_footer_controls_tool_and_verification_results(
    output, status, succeeded, verification
):
    arguments = {"command": "python3 -m unittest test_app.py -v"}
    action = extract_tool_actions(_trace(_step("bash", arguments, output=output, status=status)))[0]
    rows = derive_verification_actions(
        [
            CodingToolAction(
                action_id="call-dsh",
                source_span_id="span-dsh",
                name="bash",
                arguments=arguments,
                result=output,
                status=status,
            )
        ]
    )

    assert action.succeeded is succeeded
    assert action.operations == frozenset({"shell", "test"})
    assert len(rows) == 1
    assert rows[0].result == verification
    assert rows[0].source_span_id == "span-dsh"


@pytest.mark.parametrize(
    "output",
    [
        "[exit code: unknown]",
        "[exit code: 1.0]",
        "[exit code: 1] trailing prose",
        "The docs mention [exit code: 1]",
        "[exit code: 1]\nFurther documentation",
        {"stdout": "[exit code: 1]"},
    ],
)
def test_dsh_exit_footer_requires_a_plain_terminal_carrier(output):
    arguments = {"command": "python3 -m unittest test_app.py -v"}
    action = extract_tool_actions(
        _trace(_step("bash", arguments, output=output, status="unknown"))
    )[0]
    rows = derive_verification_actions(
        [
            CodingToolAction(
                action_id="call-dsh",
                source_span_id="span-dsh",
                name="bash",
                arguments=arguments,
                result=output,
            )
        ]
    )

    assert action.succeeded is None
    assert rows[0].result == "unknown"


@pytest.mark.parametrize("name", ["read", "read_file", "edit", "search", "unknown_tool"])
def test_dsh_exit_footer_in_file_content_does_not_mark_non_shell_tool_failed(name):
    action = extract_tool_actions(
        _trace(_step(name, {"file_path": "docs/shell.md"}, output="[exit code: 1]"))
    )[0]

    assert action.succeeded is True
    assert "shell" not in action.operations
    assert "test" not in action.operations


@pytest.mark.parametrize("name", ["read_shell_documentation", "exec_read"])
def test_dsh_exit_footer_does_not_override_a_tool_with_explicit_read_semantics(name):
    action = extract_tool_actions(
        _trace(_step(name, {"file_path": "docs/shell.md"}, output="[exit code: 1]"))
    )[0]

    assert "read" in action.operations
    assert action.succeeded is True


def test_dsh_exit_footer_cannot_be_inferred_from_a_command_argument_alone():
    action = extract_tool_actions(
        _trace(
            _step(
                "unknown_tool", {"command": "pytest -q"}, output="[exit code: 1]", status="unknown"
            )
        )
    )[0]

    assert "shell" in action.operations
    assert action.succeeded is None


def test_dsh_shell_footer_wins_over_example_exit_codes_in_stdout():
    action = extract_tool_actions(
        _trace(
            _step(
                "bash",
                {"command": "cat docs/shell.md"},
                output=(
                    "[stdout]\nExample error: process exited with code 1\n"
                    "[exit code: 1]\n[exit code: 0]"
                ),
            )
        )
    )[0]

    assert action.succeeded is True
    assert "test" not in action.operations


def test_structured_exit_code_keeps_priority_over_dsh_footer_text():
    action = extract_tool_actions(
        _trace(
            _step(
                "bash",
                {"command": "pytest -q"},
                output={
                    "exit_code": 1,
                    "stdout": "[exit code: 0]",
                },
            )
        )
    )[0]

    assert action.succeeded is False


def test_extract_tool_actions_reads_plain_shell_command():
    trace = _trace(_step("Bash", "rm -rf /"))

    action = extract_tool_actions(trace)[0]

    assert action.command == "rm -rf /"
    assert action.operations == frozenset({"shell", "write"})
    assert action.is_code_change is False


@pytest.mark.parametrize(
    ("command", "expected_paths"),
    [
        (
            "sed -n '1,190p' tests/unit/diagnoser/test_safety_rules.py",
            ("tests/unit/diagnoser/test_safety_rules.py",),
        ),
        ("cat src/app.py", ("src/app.py",)),
        (
            "git diff --cached -- tests/unit/diagnoser/test_safety_rules.py",
            ("tests/unit/diagnoser/test_safety_rules.py",),
        ),
        (
            "nl -ba tests/unit/diagnoser/test_safety_rules.py | sed -n '35,78p'",
            ("tests/unit/diagnoser/test_safety_rules.py",),
        ),
        (
            "sed -n '50,85p' tests/unit/diagnoser/test_safety_rules.py && "
            "git diff -- src/agentlens/diagnoser/executors/rule/builtin/common/safety.py "
            "tests/unit/diagnoser/test_safety_rules.py docs/diagnoser/README.md | tail -n 180",
            (
                "tests/unit/diagnoser/test_safety_rules.py",
                "src/agentlens/diagnoser/executors/rule/builtin/common/safety.py",
                "docs/diagnoser/README.md",
            ),
        ),
        (
            "export PATH=/usr/bin; sed -n '760,925p' "
            "tests/unit/diagnoser/test_builtin_rule_executor.py; "
            "sed -n '1,150p' tests/unit/diagnoser/test_coding_otel_contract.py",
            (
                "tests/unit/diagnoser/test_builtin_rule_executor.py",
                "tests/unit/diagnoser/test_coding_otel_contract.py",
            ),
        ),
        (
            ".venv/bin/pytest tests/unit/diagnoser/test_safety_rules.py "
            "tests/unit/diagnoser/test_builtin_rule_executor.py -q",
            (
                "tests/unit/diagnoser/test_safety_rules.py",
                "tests/unit/diagnoser/test_builtin_rule_executor.py",
            ),
        ),
        ("head -n 20 tests/test_config.py", ("tests/test_config.py",)),
        (
            "rg -n 'api_key' tests/unit/diagnoser/test_safety_rules.py",
            ("tests/unit/diagnoser/test_safety_rules.py",),
        ),
        (
            "bash -c 'sed -n \"1,10p\" tests/test_config.py'",
            ("tests/test_config.py",),
        ),
        (
            "cat tests/test_config.py src/app.py",
            ("tests/test_config.py", "src/app.py"),
        ),
    ],
)
def test_extract_tool_actions_extracts_paths_from_read_only_shell_commands(command, expected_paths):
    action = extract_tool_actions(_trace(_step("tool Bash", {"command": command})))[0]

    assert action.paths == expected_paths
    assert action.is_code_change is False


def test_extract_tool_actions_extracts_code_path_from_shell_redirect():
    trace = _trace(_step("Bash", "printf 'print(1)' > src/generated.py"))

    action = extract_tool_actions(trace)[0]

    assert action.operations == frozenset({"shell", "write"})
    assert action.paths == ("src/generated.py",)
    assert action.is_code_change is True


def test_tool_action_signature_canonicalizes_equivalent_json_inputs():
    first = _trace(_step("tool edit", '{"filePath":"a.py","oldString":"a","newString":"b"}'))
    second = _trace(_step("tool edit", '{"newString":"b","oldString":"a","filePath":"a.py"}'))

    assert extract_tool_actions(first)[0].signature == extract_tool_actions(second)[0].signature


def test_extract_tool_actions_recognizes_apply_patch_as_code_change():
    trace = _trace(
        _step(
            "apply_patch",
            "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch",
        )
    )

    action = extract_tool_actions(trace)[0]

    assert action.operations == frozenset({"edit"})
    assert action.paths == ("src/app.py",)
    assert action.old_content == "old"
    assert action.new_content == "new"
    assert action.is_code_change is True


def test_extract_tool_actions_does_not_mix_multiple_apply_patch_files():
    action = extract_tool_actions(
        _trace(
            _step(
                "apply_patch",
                """*** Begin Patch
*** Update File: src/first.py
@@
-old
+new
*** Update File: src/second.py
@@
-before
+after
*** End Patch""",
            )
        )
    )[0]

    assert action.old_content is None
    assert action.new_content is None


def test_extract_tool_actions_classifies_search_tools_as_reads():
    trace = _trace(
        _step("tool grep", {"pattern": "needle", "path": "src"}),
        _step("tool glob", {"pattern": "**/*.py"}),
        _step("tool grep_search", {"query": "needle"}),
    )

    actions = extract_tool_actions(trace)

    assert [action.operations for action in actions] == [
        frozenset({"read"}),
        frozenset({"read"}),
        frozenset({"read"}),
    ]


def test_extract_tool_actions_does_not_parse_json_description_as_redirect_path():
    trace = _trace(
        _step(
            "tool Bash",
            json.dumps(
                {
                    "command": "cat task.output 2>/dev/null",
                    "description": "查看输出",
                }
            ),
        )
    )

    action = extract_tool_actions(trace)[0]

    assert action.paths == ("task.output", "/dev/null")
    assert action.is_code_change is False


def test_extract_tool_actions_redirect_only_counts_the_target_as_changed():
    read = extract_tool_actions(
        _trace(_step("tool Bash", {"command": "cat src/app.py 2>/dev/null"}))
    )[0]
    write = extract_tool_actions(
        _trace(_step("tool Bash", {"command": "cat templates/app.py > src/generated.py"}))
    )[0]

    assert read.paths == ("src/app.py", "/dev/null")
    assert read.is_code_change is False
    assert write.paths == ("src/generated.py",)
    assert write.is_code_change is True


def test_extract_tool_actions_does_not_treat_document_patch_as_code_change():
    action = extract_tool_actions(
        _trace(
            _step(
                "tool Bash",
                {
                    "command": (
                        "apply_patch <<'PATCH'\n"
                        "*** Update File: docs/guide.md\n"
                        "@@\n"
                        "-old\n"
                        "+new\n"
                        "*** End Patch\n"
                        "PATCH"
                    )
                },
            )
        )
    )[0]

    assert action.paths == ("docs/guide.md",)
    assert action.is_code_change is False


def test_extract_tool_actions_decodes_escaped_patch_payload_paths():
    action = extract_tool_actions(
        _trace(
            _step(
                "tool Bash",
                {
                    "command": (
                        'const patch = "*** Begin Patch\\n'
                        "*** Update File: docs/guide.md\\n"
                        "@@\\n-old\\n+new\\n"
                        '*** End Patch"; text(await tools.apply_patch(patch));'
                    )
                },
            )
        )
    )[0]

    assert action.paths == ("docs/guide.md",)
    assert action.old_content == "old"
    assert action.new_content == "new"
    assert action.is_code_change is False


def test_extract_tool_actions_treats_mixed_escaped_patch_as_code_change():
    action = extract_tool_actions(
        _trace(
            _step(
                "tool Bash",
                {
                    "command": (
                        'const patch = "*** Begin Patch\\n'
                        "*** Update File: docs/guide.md\\n@@\\n-doc\\n+doc\\n"
                        "*** Update File: src/app.py\\n@@\\n-old\\n+new\\n"
                        '*** End Patch"; text(await tools.apply_patch(patch));'
                    )
                },
            )
        )
    )[0]

    assert action.paths == ("docs/guide.md", "src/app.py")
    assert action.is_code_change is True


@pytest.mark.parametrize(
    "command",
    [
        "git apply changes.patch",
        "patch -p1 < changes.patch",
        "git am fix.patch",
        "apply_patch < changes.patch",
    ],
)
def test_extract_tool_actions_does_not_assume_unknown_patch_target_is_code(command):
    action = extract_tool_actions(_trace(_step("tool Bash", {"command": command})))[0]

    assert action.paths == ()
    assert action.is_code_change is False


@pytest.mark.parametrize(
    "command",
    [
        "ruff check src tests",
        "mypy src",
        "pyright",
        "pnpm run typecheck",
        "npm run lint",
        "npm run build",
        "cargo check",
        "cargo clippy --all-targets -- -D warnings",
        "go vet ./...",
        "mvn verify",
        "./gradlew check",
        "./gradlew :app:test",
        "mvn -pl service -DskipTests=false test",
        "dotnet build",
        "python manage.py test",
        "django-admin test",
        "make -C backend test",
        "make -f Makefile lint",
        "just test",
        "task check",
        "pre-commit run --all-files",
        "poetry run test",
        "hatch run lint",
        "python -m django test",
        "deno test",
        "flutter test",
        "cargo nextest run",
        "ctest --test-dir build",
        "ninja -C build",
        "python -m compileall src",
        "python -m py_compile src/app.py",
        "shellcheck scripts/deploy.sh",
        "luac -p plugin.lua",
        "stylua --check plugin.lua",
        "nvim --headless '+qa'",
        "node --experimental-vm-modules /tmp/check_hast.mjs",
    ],
)
def test_extract_tool_actions_treats_quality_checks_as_verification(command):
    action = extract_tool_actions(_trace(_step("tool Bash", {"command": command})))[0]

    assert "test" in action.operations
    assert "write" not in action.operations


def test_extract_tool_actions_reads_structured_verification_result():
    action = extract_tool_actions(
        _trace(
            _step(
                "tool Bash",
                {"command": "quality-gate"},
                output={"verification_status": "failed"},
            )
        )
    )[0]

    assert "test" in action.operations
    assert action.succeeded is False


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cat <<'PY'\npytest -q\nPY", False),
        (
            (
                "apply_patch <<'PATCH'\n*** Update File: src/app.py\n@@\n"
                "-print('old')\n+print('pytest')\n*** End Patch\nPATCH",
            ),
            False,
        ),
        ("uv run pytest -q", True),
        ("npx vitest run", True),
        ("./scripts/verify.sh", True),
    ],
)
def test_extract_tool_actions_filters_embedded_text_and_accepts_wrapped_verification(
    command, expected
):
    action = extract_tool_actions(_trace(_step("tool Bash", {"command": command})))[0]

    assert ("test" in action.operations) is expected


def test_extract_tool_actions_ignores_plain_verification_words_in_output():
    action = extract_tool_actions(
        _trace(
            _step(
                "tool Bash",
                {"command": "cat reports/latest.txt"},
                output="pytest passed in the previous run",
            )
        )
    )[0]

    assert "test" not in action.operations


@pytest.mark.parametrize(
    "name",
    [
        "tool run_tests",
        "tool lint",
        "tool typecheck",
        "tool compile",
        "tool build",
        "tool validate",
        "tool smoke_check",
    ],
)
def test_extract_tool_actions_treats_verification_tool_names_as_tests(name):
    action = extract_tool_actions(_trace(_step(name, {})))[0]

    assert action.operations == frozenset({"test"})


@pytest.mark.parametrize(
    "command",
    [
        "cp templates/app.py src/app.py",
        "mv src/old.ts src/new.ts",
        "rm src/obsolete.go",
        "sed -i '' 's/a/b/' src/app.py",
        "gofmt -w cmd/main.go",
        "prettier --write src/app.ts",
        "ruff format src/app.py",
        "npm install zod",
    ],
)
def test_extract_tool_actions_recognizes_shell_file_mutations(command):
    action = extract_tool_actions(_trace(_step("tool Bash", {"command": command})))[0]

    assert "write" in action.operations
    assert action.is_code_change is True


def test_extract_tool_actions_distinguishes_formatter_check_from_write():
    check, write = extract_tool_actions(
        _trace(
            _step("tool Bash", {"command": "ruff format --check src/app.py"}),
            _step("tool Bash", {"command": "ruff format src/app.py"}),
        )
    )

    assert check.operations == frozenset({"shell", "test"})
    assert check.is_code_change is False
    assert write.operations == frozenset({"shell", "write"})
    assert write.is_code_change is True


def test_extract_tool_actions_reads_nested_protocol_fields():
    action = extract_tool_actions(
        _trace(
            _step(
                "filesystem_edit_file",
                {
                    "arguments": {
                        "targetPath": "src/nested.ts",
                        "oldText": "before",
                        "newText": "after",
                    }
                },
            )
        )
    )[0]

    assert action.operations == frozenset({"edit"})
    assert action.paths == ("src/nested.ts",)
    assert action.old_content == "before"
    assert action.new_content == "after"
    assert action.is_code_change is True


def test_extract_tool_actions_does_not_treat_task_control_as_file_write():
    actions = extract_tool_actions(
        _trace(
            _step("tool TaskCreate", {"subject": "work"}),
            _step("tool TaskUpdate", {"status": "done"}),
        )
    )

    assert [action.operations for action in actions] == [frozenset(), frozenset()]


def test_extract_tool_actions_handles_prefixed_mutation_and_verification_tools():
    delete, lint = extract_tool_actions(
        _trace(
            _step("mcp filesystem.delete_file", {"path": "src/obsolete.py"}),
            _step("mcp quality.run_lint", {}),
        )
    )

    assert delete.operations == frozenset({"write"})
    assert delete.is_code_change is True
    assert lint.operations == frozenset({"test"})


@pytest.mark.parametrize(
    "command",
    [
        "ruff check --fix src",
        "eslint --fix src/app.ts",
        "cargo fmt",
        "terraform fmt",
    ],
)
def test_extract_tool_actions_recognizes_fixing_checks_as_writes(command):
    action = extract_tool_actions(_trace(_step("tool Bash", {"command": command})))[0]

    assert action.operations in {
        frozenset({"shell", "write", "test"}),
        frozenset({"shell", "write"}),
    }
    assert action.is_code_change is True


def test_extract_tool_actions_treats_git_apply_check_as_verification_only():
    action = extract_tool_actions(
        _trace(_step("tool Bash", {"command": "git apply --check changes.patch"}))
    )[0]

    assert action.operations == frozenset({"shell", "test"})
    assert action.is_code_change is False
