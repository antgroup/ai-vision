"""Normalize heterogeneous coding-agent tool steps into shared facts."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, replace
from typing import Any, Literal

from agentlens.service.datasource.projection.models import ExecutionStep, Projection

ToolOperation = Literal["read", "write", "edit", "shell", "test"]

_TEST_COMMAND_RE = re.compile(
    r"\b(?:pytest|py\.test|unittest|nose2?|tox|nox|jest|vitest|mocha|ava|rspec|phpunit|busted)\b"
    r"|\bgo\s+(?:test|vet)\b|\bstaticcheck\b|\bgolangci-lint\b"
    r"|\bcargo\s+(?:test|check|clippy|build)\b|\bcargo\s+fmt\b[^\n]*--check"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|lint|type[-_:]?check|check|build|verify)\b"
    r"|\b(?:npx\s+)?(?:eslint|tsc|biome\s+check|prettier\s+--check|stylelint)\b"
    r"|\b(?:mvn|mvnw)\b[^\n;&|]*\b(?:test|verify|package)\b"
    r"|(?:^|[\s/])gradlew?\s+[^\n;&|]*(?:\b(?:test|check|build)\b|:test\b)"
    r"|\bdotnet\s+(?:test|build)\b|\bdotnet\s+format\b[^\n]*--verify-no-changes"
    r"|(?:(?:^|[;&|]\s*)(?:env\s+|sudo\s+)?(?:\S+/)?make\b[^\n;&|]*\b(?:test|check|lint|build|verify|smoke)\b)"
    r"|(?:(?:^|[;&|]\s*)(?:env\s+|sudo\s+)?(?:\S+/)?(?:ctest|ninja)\b)|\bcmake\s+--build\b"
    r"|\b(?:bazel|buck|pants)\s+(?:test|check|lint|build)\b|\bdocker\s+build\b"
    r"|\bterraform\s+(?:validate|plan)\b|\bterraform\s+fmt\b[^\n]*-{1,2}check"
    r"|\bhelm\s+lint\b|\b(?:ansible-lint|actionlint|hadolint|yamllint|markdownlint|sqlfluff)\b"
    r"|\b(?:semgrep|trivy|pip-audit)\b|\b(?:npm|pnpm|yarn)\s+audit\b"
    r"|\b(?:xcodebuild|mix\s+(?:test|compile)|sbt\s+(?:test|compile)|lein\s+test)\b"
    r"|\b(?:ruff\s+check|mypy|pyright|pylint|flake8|bandit)\b"
    r"|\b(?:ruff\s+format|black|isort|stylua)\b[^\n]*--check"
    r"|\bpython(?:3)?\s+-m\s+(?:pytest|unittest|compileall|py_compile)\b"
    r"|\b(?:shellcheck|bash\s+-n|sh\s+-n|luac\s+-p)\b"
    r"|\bgit\s+(?:apply\b[^\n]*--check|diff\s+--check)\b"
    r"|\bnvim\s+--headless\b|\bswift\s+(?:test|build)\b"
    r"|\b(?:python(?:3)?\s+(?:manage\.py|setup\.py)\s+test|django-admin(?:\.py)?\s+test)\b"
    r"|\b(?:just|task|rake|meson)\s+(?:test|check|lint|build|verify|smoke)\b"
    r"|\b(?:pre-commit|lefthook)\s+run\b"
    r"|\b(?:poetry|pdm|hatch|mise|uv|pipenv)\s+run\s+(?:test|lint|type[-_:]?check|check|build|verify|smoke|pytest|ruff|mypy|pyright)\b"
    r"|\b(?:npx|npm\s+exec|pnpm\s+exec|bun\s+x)\s+(?:--[^\s]+\s+)*(?:pytest|vitest|jest|mocha|eslint|tsc|ruff|mypy|pyright)\b"
    r"|(?:(?:^|[;&|]\s*)(?:\./|/)?[\w./-]*(?:test|tests|check|verify|lint|build|smoke)[\w./-]*\.(?:sh|bash|zsh|py|js|mjs|cjs|ts|tsx|rb)\b)"
    r"|\bpython(?:3)?\s+-m\s+django\s+test\b"
    r"|\b(?:deno|flutter)\s+(?:test|lint|analyze|build)\b"
    r"|\bcargo\s+nextest\s+run\b"
    r"|\b(?:python(?:3)?|node|bash|sh|ruby)\b[^\n]*[\s/](?:test|check|verify|smoke)[\w.-]*\.(?:py|js|mjs|cjs|sh|rb)\b",
    re.IGNORECASE,
)
_PATCH_APPLY_RE = re.compile(r"\b(?:apply_patch|git\s+(?:apply|am)|patch\s+-p\d*)\b", re.I)
_SHELL_WRITE_RE = re.compile(
    r"\bapply_patch\b|\bgit\s+(?:apply|am)\b|\bpatch\s+-p\d*\b|\bsed\s+-i\b|\bperl\s+-pi\b"
    r"|(?:^|[;&|]\s*)(?:cat|printf|echo)\b[^\n]*(?<![\d>])(?:>>|>)\s*[^&|;]+"
    r"|\btee\s+(?:-a\s+)?[^\s]+|(?:^|[;&|]\s*)(?:cp|mv|rm|touch|install)\b",
    re.IGNORECASE,
)
_PATH_ARGUMENT_MUTATION_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:cp|mv|rm|touch|install)\b|\bsed\s+-i\b|\bperl\s+-pi\b",
    re.I,
)
_FORMATTER_WRITE_RE = re.compile(
    r"\b(?:gofmt\s+-w|goimports\s+-w|clang-format\s+-i|prettier\s+--write"
    r"|ruff\s+format|ruff\s+check\b[^\n]*--fix|eslint\b[^\n]*--fix"
    r"|biome\s+check\b[^\n]*--write|stylelint\b[^\n]*--fix"
    r"|black|rustfmt|cargo\s+fmt|stylua|terraform\s+fmt)\b",
    re.I,
)
_PACKAGE_MUTATION_RE = re.compile(
    r"\b(?:npm\s+install|pnpm\s+add|yarn\s+add|bun\s+add|cargo\s+add|go\s+get|composer\s+require)\b"
    r"|\bdotnet\s+add\b[^\n]*\bpackage\b",
    re.I,
)

_CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".java",
        ".kt",
        ".kts",
        ".rs",
        ".rb",
        ".c",
        ".cpp",
        ".cc",
        ".h",
        ".hpp",
        ".cs",
        ".swift",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
        ".php",
        ".lua",
        ".pl",
        ".r",
        ".m",
        ".mm",
        ".vue",
        ".svelte",
        ".sql",
        ".html",
        ".css",
        ".scss",
        ".less",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".gradle",
        ".properties",
        ".ini",
        ".cfg",
        ".conf",
        ".lock",
    }
)
_PATH_KEYS = frozenset(
    {
        "path",
        "filepath",
        "filepaths",
        "targetfile",
        "targetpath",
        "absolutepath",
        "filename",
        "file",
        "files",
        "destination",
        "destinationpath",
        "dest",
        "outputpath",
        "newpath",
        "oldpath",
    }
)
_COMMAND_KEYS = frozenset({"command", "cmd", "commandline", "script"})
_OLD_KEYS = frozenset({"oldstring", "oldstr", "oldtext", "targetcontent", "oldcontent"})
_NEW_KEYS = frozenset(
    {"newstring", "newstr", "newtext", "replacementcontent", "newcontent", "codecontent"}
)
_CODE_FILENAMES = frozenset(
    {
        "dockerfile",
        "makefile",
        "justfile",
        "build",
        "workspace",
        "pyproject.toml",
        "package.json",
        "tsconfig.json",
        "cargo.toml",
        "go.mod",
        "go.sum",
        "requirements.txt",
        "gemfile",
        "composer.json",
    }
)
_EXIT_CODE_KEYS = frozenset({"exitcode", "returncode", "statuscode"})
_VERIFICATION_RESULT_KEYS = frozenset(
    {
        "buildresult",
        "buildstatus",
        "checkresult",
        "checkstatus",
        "lintresult",
        "lintstatus",
        "smokeresult",
        "smokestatus",
        "testresult",
        "teststatus",
        "typecheckresult",
        "typecheckstatus",
        "validationresult",
        "validationstatus",
        "verificationresult",
        "verificationstatus",
    }
)
_VERIFICATION_SUCCESS_VALUES = frozenset({"ok", "pass", "passed", "success", "succeeded", "true"})
_VERIFICATION_FAILURE_VALUES = frozenset({"error", "fail", "failed", "failure", "false"})
_PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", re.MULTILINE)
_REDIRECT_PATH_RE = re.compile(r"(?:>>|>)\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|]+))")
_TEE_PATH_RE = re.compile(r"\btee\s+(?:-a\s+)?(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|]+))", re.I)


_READ_COMMANDS = frozenset(
    {"awk", "bat", "cat", "cut", "grep", "head", "less", "more", "nl", "rg", "sed", "tail"}
)
_GIT_READ_SUBCOMMANDS = frozenset({"blame", "diff", "grep", "show"})
_TEST_READ_COMMANDS = frozenset({"nose", "nose2", "py.test", "pytest", "unittest"})
_TEST_OPTIONS_WITH_VALUES = frozenset(
    {
        "--confcutdir",
        "--deselect",
        "--ignore",
        "--keyword",
        "--markexpr",
        "--maxfail",
        "--override-ini",
        "--tb",
        "-k",
        "-m",
        "-s",
    }
)
_READ_OPTIONS_WITH_VALUES = {
    "awk": frozenset({"-f", "--file", "-v", "--assign"}),
    "cut": frozenset(
        {"-b", "--bytes", "-c", "--characters", "-d", "--delimiter", "-f", "--fields"}
    ),
    "grep": frozenset({"-A", "-B", "-C", "-e", "--regexp", "-f", "--file", "-m", "--max-count"}),
    "head": frozenset({"-c", "--bytes", "-n", "--lines"}),
    "rg": frozenset(
        {
            "-A",
            "-B",
            "-C",
            "-e",
            "--regexp",
            "-f",
            "--file",
            "-g",
            "--glob",
            "-m",
            "--max-count",
            "--max-filesize",
            "--type",
            "--type-add",
            "--type-not",
        }
    ),
    "sed": frozenset({"-e", "--expression", "-f", "--file"}),
    "tail": frozenset({"-c", "--bytes", "-n", "--lines"}),
}
_READ_PATTERN_OPTIONS = {
    "awk": frozenset({"-f", "--file"}),
    "grep": frozenset({"-e", "--regexp", "-f", "--file"}),
    "rg": frozenset({"-e", "--regexp", "-f", "--file"}),
    "sed": frozenset({"-e", "--expression", "-f", "--file"}),
}
_SHELL_CONTROL_TOKENS = frozenset({"&", ";", "&&", "|", "||"})
_REDIRECT_TOKEN_RE = re.compile(r"^\d*(?:>>?|<<?)")


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _structured(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text or text[0] not in "{[":
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        return str(value)


def _walk_fields(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk_fields(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_fields(child)


def _first_value(data: dict[str, Any], aliases: frozenset[str]) -> Any:
    for key, value in _walk_fields(data):
        if _normalized_key(key) in aliases and value not in (None, ""):
            return value
    return None


def _command_text(value: Any) -> str | None:
    """Normalize a command argument into a shell-parseable string.

    Coding runtimes disagree on the shape of the command argument.  Some send a
    plain string; the Codex shell tool sends an argv array such as
    ``["/bin/zsh", "-lc", "chmod -R 777 /tmp/x"]``.  Calling ``str()`` on that
    list yields a Python repr whose brackets and commas break shell tokenizing,
    so the safety rules never see the real command and high-risk executions go
    undetected.  Rejoin argv with ``shlex.join`` to recover a string the
    detector can unwrap (``zsh -lc '...'``) and classify.
    """
    if isinstance(value, str):
        return value or None
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value if item not in (None, "")]
        if not parts:
            return None
        if len(parts) == 1:
            # A single-element sequence carries the whole command string, not an
            # argv vector; quoting it via shlex.join would wrap it in shell
            # escapes and defeat downstream patch/heredoc recognition.
            return parts[0] or None
        try:
            return shlex.join(parts)
        except (TypeError, ValueError):
            return " ".join(parts)
    if value is None:
        return None
    return str(value)


def _paths(data: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key, value in _walk_fields(data):
        if _normalized_key(key) not in _PATH_KEYS:
            continue
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate and candidate not in values:
                values.append(candidate)
    return tuple(values)


def _shell_write_paths(command: str) -> tuple[str, ...]:
    values: list[str] = []
    command = _normalize_inline_patch(command)
    for match in _PATCH_PATH_RE.finditer(command):
        candidate = match.group(1).strip()
        if candidate and candidate not in values:
            values.append(candidate)
    for pattern in (_REDIRECT_PATH_RE, _TEE_PATH_RE):
        for match in pattern.finditer(command):
            candidate = next((value for value in match.groups() if value), "").strip()
            if candidate and candidate not in values:
                values.append(candidate)
    return tuple(values)


def _normalize_inline_patch(command: str) -> str:
    """Decode escaped newlines in Patch payloads embedded in tool scripts."""
    if "*** Begin Patch" not in command and "*** Update File:" not in command:
        return command
    return command.replace("\\r\\n", "\n").replace("\\n", "\n")


def _strip_verification_payloads(command: str) -> str:
    """Remove patch and heredoc bodies before matching executable commands."""
    text = _normalize_inline_patch(command)
    text = re.sub(r"\*\*\* Begin Patch.*?\*\*\* End Patch", " ", text, flags=re.DOTALL)
    lines: list[str] = []
    heredoc_delimiter: str | None = None
    for line in text.splitlines():
        if heredoc_delimiter is not None:
            if line.strip() == heredoc_delimiter:
                heredoc_delimiter = None
            continue
        match = re.search(
            r"<<-?\s*(['\"]?)([A-Za-z_][\w-]*)\1(?:\s|$)",
            line,
        )
        if match:
            lines.append(line[: match.start()])
            heredoc_delimiter = match.group(2)
            continue
        lines.append(line)
    return "\n".join(lines)


def is_verification_command(command: str | None) -> bool:
    """Recognize verification commands without scanning embedded source text."""
    if not command:
        return False
    return bool(_TEST_COMMAND_RE.search(_strip_verification_payloads(command)))


def verification_follows_last_mutation(command: str | None) -> bool:
    """Prove order within one shell action; unsupported shell syntax is unknown.

    This does not split or rewrite canonical ToolActions. Only a straight-line
    sequence of recognized commands can establish same-action verification.
    """
    operations = _ordered_shell_operations(command or "")
    if operations is None or "mutation" not in operations:
        return False
    last_mutation = len(operations) - 1 - operations[::-1].index("mutation")
    return "verification" in operations[last_mutation + 1 :]


def verification_result_is_unmasked(command: str | None) -> bool:
    """Prove that the shell exit describes every executable check, not its tail.

    A single final check or a direct && chain of checks is supported. Unparsed
    syntax and semicolon/newline chains of checks cannot prove all passed.
    """
    if not command:
        return True  # Dedicated verification tools use their explicit result.
    segments = _linear_shell_segments(command)
    if not segments or _ordered_shell_operations(command) is None:
        return False
    parsed = [shlex.split(segment) for segment in segments]
    if (
        len(parsed) == 1
        and len(parsed[0]) == 3
        and parsed[0][0] in {"bash", "sh", "zsh", "dash"}
        and parsed[0][1] in {"-c", "-lc", "-ec"}
    ):
        return verification_result_is_unmasked(parsed[0][2])
    checks = [
        index for index, tokens in enumerate(parsed) if tokens and _executable_verification(tokens)
    ]
    if not checks or checks[-1] != len(segments) - 1:
        return False
    return all(
        later == earlier + 1
        and re.search(
            re.escape(segments[earlier]) + r"\s*&&\s*" + re.escape(segments[later]), command
        )
        for earlier, later in zip(checks, checks[1:], strict=False)
    )


def _linear_shell_segments(command: str) -> list[str] | None:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    heredoc: tuple[str, bool, bool] | None = None
    index = 0
    needs_command = False
    conditional_chain = False

    def flush() -> None:
        value = "".join(current).strip()
        if value:
            segments.append(value)
        current.clear()

    while index < len(command):
        char = command[index]
        if char == "\\" and quote != "'":
            if index + 1 == len(command):
                return None
            if command[index + 1] != "\n":
                current.extend(command[index : index + 2])
            index += 2
            continue
        if quote is not None:
            if quote == '"' and char in {"$", "`"}:
                return None
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            needs_command = False
            index += 1
            continue
        if char == "#" and (not current or current[-1].isspace()):
            index = command.find("\n", index)
            if index < 0:
                index = len(command)
            continue
        if command.startswith("<<", index):
            if heredoc is not None:
                return None
            match = re.match(r"<<(-?)\s*(['\"]?)([A-Za-z_][\w-]*)\2(?=\s|$)", command[index:])
            if match is None:
                return None
            heredoc = (match[3], bool(match[1]), bool(match[2]))
            current.append(" ")
            index += match.end()
            continue
        if char == "\n" and heredoc is not None:
            flush()
            delimiter, strip_tabs, quoted = heredoc
            index += 1
            while index < len(command):
                end = command.find("\n", index)
                if end < 0:
                    end = len(command)
                line = command[index:end]
                index = min(end + 1, len(command))
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                if not quoted and ("$" in line or "`" in line):
                    return None
            else:
                return None
            heredoc = None
            needs_command = False
            continue
        if char in "$`(){}<>|" or (char == "&" and not command.startswith("&&", index)):
            return None
        if char == ";" or command.startswith("&&", index):
            if heredoc is not None or not "".join(current).strip():
                return None
            if char == ";" and conditional_chain:
                return None
            flush()
            needs_command = char == "&"
            conditional_chain |= char == "&"
            index += 2 if char == "&" else 1
            continue
        if char in "\r\n":
            if conditional_chain and "".join(current).strip() and command[index + 1 :].strip():
                return None
            flush()
        else:
            current.append(char)
            if not char.isspace():
                needs_command = False
        index += 1
    if quote is not None or heredoc is not None or needs_command:
        return None
    flush()
    return segments


_ORDERED_MUTATORS = frozenset(
    {
        "apply_patch",
        "git",
        "patch",
        "sed",
        "perl",
        "cp",
        "mv",
        "rm",
        "touch",
        "install",
        "tee",
        "gofmt",
        "goimports",
        "clang-format",
        "prettier",
        "ruff",
        "eslint",
        "biome",
        "stylelint",
        "black",
        "rustfmt",
        "cargo",
        "stylua",
        "terraform",
    }
)
_ORDERED_READERS = _READ_COMMANDS - {"awk", "sed", "less", "more", "rg"}
_INFORMATION_ONLY_OPTIONS = frozenset(
    {
        "--help",
        "-h",
        "--version",
        "-V",
        "--collect-only",
        "--collectonly",
        "--co",
        "--setup-plan",
        "--setup-only",
        "--fixtures",
        "--funcargs",
        "--fixtures-per-test",
        "--markers",
    }
)
_SHORT_VALUE_OPTIONS = {
    "unittest": frozenset("k"),
    "pytest": frozenset("kmcopr"),
    "py.test": frozenset("kmcopr"),
}


def _information_only_invocation(tokens: list[str]) -> bool:
    """Inspect option semantics without matching letters inside option values.

    argparse combines short flags and unittest permits long-option prefixes.
    A value-taking short option consumes the rest of its cluster, so ``-khelp``
    is a selection pattern, whereas ``-qbh`` requests help. Unknown runners'
    potential help/version clusters cannot establish executable verification.
    """
    executable = tokens[0].rsplit("/", 1)[-1]
    arguments = tokens[1:]
    runner = executable
    if executable in {"python", "python3"}:
        if arguments[:1] == ["-m"] and len(arguments) > 1:
            runner = arguments[1]
            arguments = arguments[2:]
    value_options = _SHORT_VALUE_OPTIONS.get(runner, frozenset())
    if runner == "unittest" and arguments[:1] == ["discover"]:
        value_options |= frozenset("spt")
    for token in arguments:
        option = token.split("=", 1)[0]
        if option in _INFORMATION_ONLY_OPTIONS:
            return True
        # unittest's argparse accepts --h, --he and --hel as --help. For an
        # unknown runner, such a spelling cannot prove an execution either.
        if len(option) > 2 and "--help".startswith(option):
            return True
        if token.startswith("-") and not token.startswith("--"):
            for flag in token[1:]:
                if flag in value_options:
                    break
                if flag in {"h", "V"}:
                    return True
    return False


def _executable_verification(tokens: list[str]) -> bool:
    executable = tokens[0].rsplit("/", 1)[-1]
    if any(char.isspace() for char in executable):
        return False
    if _information_only_invocation(tokens):
        return False
    if executable in {"python", "python3", "node", "ruby", "bash", "sh"}:
        arguments = tokens[1:]
        if any(value in {"-c", "-e", "--eval", "--print", "-p"} for value in arguments):
            return False
        while arguments and arguments[0] in {"-u", "-B", "-I", "-O", "--"}:
            arguments = arguments[1:]
        if executable in {"python", "python3"} and arguments[:1] == ["-m"]:
            return (
                len(arguments) >= 2
                and re.fullmatch(r"[\w.]+", arguments[1]) is not None
                and is_verification_command(f"python -m {arguments[1]}")
            )
        if (
            executable in {"python", "python3"}
            and len(arguments) >= 2
            and arguments[0].rsplit("/", 1)[-1] == "manage.py"
            and arguments[1] == "test"
        ):
            return True
        return (
            bool(arguments)
            and re.search(r"[\s;&|]", arguments[0]) is None
            and is_verification_command(f"{executable} {arguments[0]}")
        )
    # Match the actual executable/subcommand, never a test word in an argument.
    normalized = " ".join([executable, *tokens[1:]])
    match = _TEST_COMMAND_RE.search(normalized)
    return match is not None and match.start() == 0


def _ordered_shell_operations(command: str, depth: int = 0) -> list[str] | None:
    if depth > 4:
        return None
    segments = _linear_shell_segments(command)
    if segments is None:
        return None
    operations: list[str] = []
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return None
        if not tokens:
            continue
        executable = tokens[0].rsplit("/", 1)[-1]
        if (
            executable in {"bash", "sh", "zsh", "dash"}
            and len(segments) == 1
            and len(tokens) == 3
            and tokens[1] in {"-c", "-lc", "-ec"}
        ):
            nested = _ordered_shell_operations(tokens[2], depth + 1)
            if nested is None:
                return None
            operations.extend(nested)
        elif executable in _ORDERED_MUTATORS and _is_shell_write(segment):
            operations.append("mutation")
        elif _executable_verification(tokens):
            operations.append("verification")
        elif executable in _ORDERED_READERS or executable in {"echo", "printf", "true"}:
            continue
        elif executable == "rg" and not any(token.startswith("--pre") for token in tokens[1:]):
            continue
        elif (
            executable == "sed"
            and len(tokens) >= 3
            and tokens[1] == "-n"
            and re.fullmatch(r"[\d,$p -]+", tokens[2])
        ):
            continue
        elif executable == "git" and len(tokens) > 1 and tokens[1] in _GIT_READ_SUBCOMMANDS:
            continue
        else:
            return None
    return operations


def _git_read_paths(tokens: list[str]) -> tuple[str, ...]:
    if not tokens or tokens[0] not in _GIT_READ_SUBCOMMANDS or "--" not in tokens:
        return ()
    separator = tokens.index("--")
    values: list[str] = []
    for token in tokens[separator + 1 :]:
        if token in _SHELL_CONTROL_TOKENS:
            break
        token = token.strip("'\"`()[]{}:,;")
        if token and token != "-" and token not in values:
            values.append(token)
    return tuple(values)


def _test_read_paths(tokens: list[str]) -> tuple[str, ...]:
    values: list[str] = []
    skip_next = False
    for token in tokens:
        if token in _SHELL_CONTROL_TOKENS:
            break
        if skip_next:
            skip_next = False
            continue
        if token in _TEST_OPTIONS_WITH_VALUES:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        candidate = token.strip("'\"`()[]{}:,;")
        if candidate in {".", ".."} or not candidate:
            continue
        if (
            "/" in candidate
            or candidate.lower() in {"docs", "src", "test", "tests"}
            or re.search(
                r"\.(?:c|cc|cpp|cs|go|java|js|json|kt|md|py|rb|rs|sh|sql|toml|ts|tsx|xml|yaml|yml)$",
                candidate,
                re.I,
            )
        ) and candidate not in values:
            values.append(candidate)
    return tuple(values)


def _shell_read_path_tokens(tokens: list[str]) -> tuple[str, ...]:
    if not tokens:
        return ()

    if any(token in _SHELL_CONTROL_TOKENS for token in tokens):
        segment_values: list[str] = []
        segment: list[str] = []
        for token in tokens:
            if token in _SHELL_CONTROL_TOKENS:
                for candidate in _shell_read_path_tokens(segment):
                    if candidate not in segment_values:
                        segment_values.append(candidate)
                segment = []
            else:
                segment.append(token)
        for candidate in _shell_read_path_tokens(segment):
            if candidate not in segment_values:
                segment_values.append(candidate)
        return tuple(segment_values)

    executable = tokens[0].rsplit("/", 1)[-1].lower()
    if executable == "git":
        return _git_read_paths(tokens[1:])
    if executable in {"bash", "sh", "zsh"} and "-c" in tokens:
        nested_tokens = tokens[tokens.index("-c") + 1 :]
        nested = nested_tokens[0] if len(nested_tokens) == 1 else " ".join(nested_tokens)
        return _shell_read_paths(nested)
    if executable in _TEST_READ_COMMANDS:
        return _test_read_paths(tokens[1:])
    if executable not in _READ_COMMANDS:
        return ()

    option_values = _READ_OPTIONS_WITH_VALUES.get(executable, frozenset())
    positional: list[str] = []
    skip_next = False
    end_options = False
    for token in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if _REDIRECT_TOKEN_RE.match(token):
            if token in {">", ">>", "<", "<<", "1>", "1>>", "2>", "2>>"}:
                skip_next = True
            continue
        if token == "--":
            end_options = True
            continue
        if not end_options and token.startswith("-"):
            if token in option_values:
                skip_next = True
            continue
        token = token.strip("'\"`()[]{}:,;")
        if token and token != "-":
            positional.append(token)

    pattern_options = _READ_PATTERN_OPTIONS.get(executable, frozenset())
    has_pattern_option = any(
        token in pattern_options
        or any(token.startswith(f"{option}=") for option in pattern_options)
        for token in tokens[1:]
    )
    if executable in {"awk", "sed"} and not has_pattern_option and positional:
        positional = positional[1:]
    elif executable in {"grep", "rg"} and not has_pattern_option:
        listing_mode = "--files" in tokens or "--files-with-matches" in tokens
        if not listing_mode and positional:
            positional = positional[1:]

    values: list[str] = []
    for path in positional:
        if path not in values:
            values.append(path)
    return tuple(values)


def _shell_tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def _shell_read_paths(command: str) -> tuple[str, ...]:
    """Extract file operands from direct or compound read-only shell commands."""
    return _shell_read_path_tokens(_shell_tokens(command))


def is_read_only_inspection_command(command: str | None) -> bool:
    """Return whether a command only inspects files without mutating or exporting them."""
    if not command:
        return True
    return bool(_shell_read_paths(command)) and not _is_shell_write(command)


def _apply_patch_replacement(command: str) -> tuple[str, str] | None:
    """Extract one exact replacement from an inline ``apply_patch`` payload.

    A patch containing multiple files cannot be represented by this action's
    single path/content tuple without mixing evidence, so leave it unparsed.
    """
    command = _normalize_inline_patch(command)
    if not (_PATCH_APPLY_RE.search(command) or "*** Begin Patch" in command):
        return None
    update_files = _PATCH_PATH_RE.findall(command)
    if len(update_files) != 1:
        return None

    old_lines: list[str] = []
    new_lines: list[str] = []
    in_target = False
    for line in command.splitlines():
        if line.startswith("*** Update File:"):
            in_target = True
            continue
        if not in_target:
            continue
        if line.startswith("*** "):
            break
        if line.startswith("-") and not line.startswith("---"):
            old_lines.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            new_lines.append(line[1:])

    if not old_lines or not new_lines:
        return None
    return "\n".join(old_lines), "\n".join(new_lines)


def _command_code_paths(command: str) -> tuple[str, ...]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    values: list[str] = []
    for token in tokens:
        candidate = token.strip("'\"`()[]{}:,;")
        if is_code_path(candidate) and candidate not in values:
            values.append(candidate)
    return tuple(values)


def _is_shell_write(command: str) -> bool:
    if _PATCH_APPLY_RE.search(command) and re.search(r"--check\b", command, re.I):
        return False
    if _SHELL_WRITE_RE.search(command) or _PACKAGE_MUTATION_RE.search(command):
        return True
    if not _FORMATTER_WRITE_RE.search(command):
        return False
    return not re.search(r"(?:-{1,2}check|--diff|--verify-no-changes)\b", command, re.I)


def _explicit_exit_code(output: Any, *, tool_name: str = "") -> int | None:
    data = _structured(output)
    for key, value in data.items():
        if _normalized_key(key) not in _EXIT_CODE_KEYS:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    named_operations = _tool_name_operations(tool_name)
    if (
        "shell" in named_operations
        and not named_operations.intersection({"read", "write", "edit"})
        and isinstance(output, str)
    ):
        # DSH appends this carrier after stdout/stderr. Only a named Shell
        # tool's independent final line is metadata; file content and embedded
        # examples are ordinary output. Prefer the footer over such examples.
        footer = re.search(r"(?:^|\n)\[exit code: (-?\d+)\][ \t]*(?:\r?\n)?\Z", output)
        if footer:
            return int(footer.group(1))
    text = _text(output)
    match = re.search(
        r"(?:process exited with code|exit status|exit_code[\"']?\s*[:=])\s*(-?\d+)",
        text,
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def _structured_verification_status(value: Any) -> tuple[bool | None, bool]:
    """Read only explicitly named verification results from structured output."""
    data = _structured(value)
    if not data:
        return None, False
    for key, child in _walk_fields(data):
        if _normalized_key(key) not in _VERIFICATION_RESULT_KEYS:
            continue
        if isinstance(child, bool):
            return child, True
        normalized = str(child).strip().lower()
        if normalized in _VERIFICATION_SUCCESS_VALUES:
            return True, True
        if normalized in _VERIFICATION_FAILURE_VALUES:
            return False, True
        return None, True
    return None, False


def _status(step: ExecutionStep) -> bool | None:
    exit_code = _explicit_exit_code(step.output, tool_name=step.name or "")
    if exit_code is not None:
        return exit_code == 0
    status = (step.status or "").upper()
    if status in {"ERROR", "FAILED", "FAILURE"}:
        return False
    if status in {"OK", "SUCCESS"}:
        return True
    return None


def _tool_name_operations(name: str) -> set[ToolOperation]:
    normalized = _normalized_key(name.removeprefix("tool ").removeprefix("builtin "))
    operations: set[ToolOperation] = set()
    if (
        any(token in normalized for token in ("replace", "edit", "applypatch", "multiedit"))
        or normalized == "patch"
        or normalized.endswith("patchfile")
    ):
        operations.add("edit")
    elif "write" in normalized or any(
        normalized.endswith(suffix)
        for suffix in (
            "createfile",
            "savefile",
            "overwritefile",
            "appendfile",
            "deletefile",
            "removefile",
            "renamefile",
            "movefile",
            "copyfile",
        )
    ):
        operations.add("write")
    if any(token in normalized for token in ("read", "viewfile", "grep", "glob", "search")):
        operations.add("read")
    if any(token in normalized for token in ("bash", "exec", "runcommand", "shell")):
        operations.add("shell")
    if any(
        normalized.endswith(suffix)
        for suffix in (
            "pytest",
            "runtest",
            "runtests",
            "test",
            "lint",
            "runlint",
            "typecheck",
            "runtypecheck",
            "compile",
            "build",
            "check",
            "validate",
            "verify",
            "smokecheck",
            "runsmokecheck",
        )
    ):
        operations.add("test")
    return operations


@dataclass(frozen=True)
class ToolAction:
    step_index: int
    tool_name: str
    operations: frozenset[ToolOperation]
    command: str | None
    paths: tuple[str, ...]
    old_content: str | None
    new_content: str | None
    succeeded: bool | None
    input_text: str
    output_text: str
    source_span_id: str | None = None
    verification_succeeded: bool | None = None
    has_verification_result: bool = False
    has_output: bool = False

    @property
    def is_code_change(self) -> bool:
        if not self.operations.intersection({"write", "edit"}):
            return False
        if any(is_code_path(path) for path in self.paths):
            return True
        command = self.command or ""
        if _PATCH_APPLY_RE.search(command):
            return any(is_code_path(path) for path in self.paths)
        return bool(
            _PACKAGE_MUTATION_RE.search(command)
            or (_FORMATTER_WRITE_RE.search(command) and _is_shell_write(command))
        )

    @property
    def signature(self) -> tuple[Any, ...]:
        name = _normalized_key(self.tool_name)
        command = " ".join((self.command or "").split())
        if command:
            return (name, "command", command)
        if self.paths or self.old_content is not None or self.new_content is not None:
            return (name, "content", self.paths, self.old_content, self.new_content)
        structured = _structured(self.input_text)
        canonical_input = _text(structured) if structured else " ".join(self.input_text.split())
        return (name, "input", canonical_input)

    @property
    def execution_context(self) -> dict[str, str]:
        """Acquired directories only; never resolve or infer a host directory."""
        arguments = _structured(self.input_text)
        return {
            key: value
            for key in ("cwd", "workdir")
            if isinstance(value := arguments.get(key), str) and value.strip()
        }

    @property
    def execution_identity(self) -> tuple[Any, ...]:
        """Conservative matching identity that preserves quoted command bytes.

        Formatting variants may remain distinct; meaningful whitespace inside
        quotes must never collapse two real check targets into one.
        """
        name = _normalized_key(self.tool_name)
        context = tuple(sorted(set(self.execution_context.values())))
        if self.command is not None:
            return (name, "command", self.command, context)
        structured = _structured(self.input_text)
        canonical_input = _text(structured) if structured else self.input_text
        return (name, "input", canonical_input, context)


def is_code_path(path: str) -> bool:
    normalized = path.lower().split("?", 1)[0].split("#", 1)[0].rstrip("/\\")
    filename = normalized.rsplit("/", 1)[-1]
    if filename in _CODE_FILENAMES:
        return True
    dot = normalized.rfind(".")
    return dot >= 0 and normalized[dot:] in _CODE_EXTENSIONS


def classify_tool_action(
    *,
    step_index: int,
    tool_name: str,
    arguments: Any,
    result: Any,
    status: str | None,
) -> ToolAction:
    """Classify one confirmed tool action from either a span or a projection."""
    data = _structured(arguments)
    operations = _tool_name_operations(tool_name)
    command_value = _first_value(data, _COMMAND_KEYS)
    if command_value in (None, "") and "shell" in operations and isinstance(arguments, str):
        command_value = arguments.strip()
    command = _command_text(command_value)
    paths = list(_paths(data))
    if command:
        operations.add("shell")
        if is_verification_command(command) or "verification" in (
            _ordered_shell_operations(command) or []
        ):
            operations.add("test")
        shell_write = _is_shell_write(command)
        if not shell_write:
            for candidate in _shell_read_paths(command):
                if candidate not in paths:
                    paths.append(candidate)
        for candidate in _shell_write_paths(command):
            if candidate not in paths:
                paths.append(candidate)
        if shell_write:
            operations.add("write")
            candidates: list[str] = []
            if _PATH_ARGUMENT_MUTATION_RE.search(command) or _FORMATTER_WRITE_RE.search(command):
                candidates.extend(_command_code_paths(command))
            for candidate in candidates:
                if candidate not in paths:
                    paths.append(candidate)
    if operations.intersection({"write", "edit"}) and not command:
        for candidate in _shell_write_paths(_text(arguments)):
            if candidate not in paths:
                paths.append(candidate)
    structured_verification_status, has_structured_verification = _structured_verification_status(
        result
    )
    if has_structured_verification:
        operations.add("test")
    old_value = _first_value(data, _OLD_KEYS)
    new_value = _first_value(data, _NEW_KEYS)
    if old_value in (None, "") or new_value in (None, ""):
        replacement = _apply_patch_replacement(command or _text(arguments))
        if replacement is not None:
            old_value = old_value if old_value not in (None, "") else replacement[0]
            new_value = new_value if new_value not in (None, "") else replacement[1]
            operations.add("edit")
    exit_code = _explicit_exit_code(result, tool_name=tool_name)
    if exit_code is not None:
        succeeded: bool | None = exit_code == 0
    elif has_structured_verification and structured_verification_status is not None:
        succeeded = structured_verification_status
    elif (status or "").upper() in {"ERROR", "FAILED", "FAILURE"}:
        succeeded = False
    elif (status or "").upper() in {"OK", "SUCCESS"}:
        succeeded = True
    else:
        succeeded = None
    return ToolAction(
        step_index=step_index,
        tool_name=tool_name,
        operations=frozenset(operations),
        command=command,
        paths=tuple(paths),
        old_content=str(old_value) if old_value not in (None, "") else None,
        new_content=str(new_value) if new_value not in (None, "") else None,
        succeeded=succeeded,
        input_text=_text(arguments),
        output_text=_text(result),
        verification_succeeded=(
            structured_verification_status if has_structured_verification else succeeded
        ),
        has_verification_result=has_structured_verification,
        has_output=result is not None,
    )


def extract_tool_actions(trace: Projection) -> list[ToolAction]:
    """Classify generic execution steps with Coding action semantics."""
    return [
        replace(
            classify_tool_action(
                step_index=step.index if step.index is not None else fallback_index,
                tool_name=step.name or "",
                arguments=step.input,
                result=step.output,
                status=step.status,
            ),
            source_span_id=step.ref or None,
        )
        for fallback_index, step in enumerate(trace.steps)
        if isinstance(step, ExecutionStep) and step.type == "TOOL"
    ]
