"""Static safety rules shared by Coding diagnosers.

Rules only classify text or normalized shell commands. Executors own trace
traversal, confirmed-execution checks, evidence shaping, and scoring.
"""

import base64
import binascii
import math
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass

MAX_SAFETY_SCAN_CHARS = 64 * 1024
MAX_SAFETY_FINDINGS = 32
_TRUNCATION_MARKER = "\n...[truncated]...\n"


@dataclass(frozen=True)
class BoundedSafetyText:
    text: str
    truncated: bool
    original_chars: int


def bound_safety_text(text: str, limit: int = MAX_SAFETY_SCAN_CHARS) -> BoundedSafetyText:
    """Keep safety matching bounded while retaining both field boundaries."""
    original_chars = len(text)
    if original_chars <= limit:
        return BoundedSafetyText(text=text, truncated=False, original_chars=original_chars)
    if limit <= 0:
        return BoundedSafetyText(text="", truncated=True, original_chars=original_chars)
    if limit <= len(_TRUNCATION_MARKER):
        return BoundedSafetyText(text=text[:limit], truncated=True, original_chars=original_chars)
    available = limit - len(_TRUNCATION_MARKER)
    head_chars = available // 2
    tail_chars = available - head_chars
    tail = text[-tail_chars:] if tail_chars else ""
    bounded = f"{text[:head_chars]}{_TRUNCATION_MARKER}{tail}"
    return BoundedSafetyText(text=bounded, truncated=True, original_chars=original_chars)


_FORMAT_ONLY_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY-----", re.I), "私钥"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}\b", re.I), "Bearer Token"),
    (
        re.compile(r"\bBasic\s+(?P<secret_value>[A-Za-z0-9+/]{12,}={0,2})(?=$|[\s,;])", re.I),
        "Basic 凭据",
    ),
    (
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+[a-z0-9]+)?|redis)://"
            r"[^\s/:@]+:(?P<secret_value>[^\s@/]{3,})@",
            re.I,
        ),
        "数据库连接凭据",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{19,}|"
            r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"glpat-[A-Za-z0-9_-]{20,}|npm_[A-Za-z0-9]{20,}|"
            r"pypi-[A-Za-z0-9_-]{16,}|(?:AKIA|ASIA)[0-9A-Z]{16})"
            r"(?![A-Za-z0-9_-])",
            re.I,
        ),
        "访问密钥",
    ),
)

_SECRET_VALUE_PATTERN = (
    r"(?P<secret_value>"
    r"\"(?:\\.|[^\"\\])*\"|"
    r"'(?:\\.|[^'\\])*'|"
    r"`(?:\\.|[^`\\])*`|"
    r"[^\s,;}\]]+"
    r")"
)
_LABELED_SECRET_FIELD_PATTERN = (
    r"api[_-]?key|secret[_-]?key|"
    r"access[_-]?key(?:[_-]?(?:id|secret))?|"
    r"secret[_-]?access[_-]?key(?:[_-]?(?:id|secret))?|"
    r"client[_-]?secret|"
    r"(?:aws|aliyun|alibaba|oss|odps)[_-]?"
    r"(?:access[_-]?key|secret[_-]?access[_-]?key|ak|sk)(?:[_-]?(?:id|secret))?|"
    r"(?!(?:access|auth|bearer|csrf|id|refresh|session|xsrf)[_-]?token"
    r"(?![A-Za-z0-9_-]))[A-Za-z][A-Za-z0-9_-]*token|"
    r"ak|sk"
)
# Require a non-empty prefix so a generic ``token`` field is not enough.
_DEFAULT_SECRET_FIELD_PATTERN = r"[A-Za-z][A-Za-z0-9_-]*token"
_DEFAULT_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"^[^\n]*?(?<![A-Za-z0-9_])['\"]?(?P<field_name>{_DEFAULT_SECRET_FIELD_PATTERN})"
            rf"['\"]?(?![A-Za-z0-9_])[^\n]*?(?P<default_marker>\bdefault\b)"
            rf"\s*[:=]?\s*{_SECRET_VALUE_PATTERN}",
            re.I | re.M,
        ),
        "密钥/Token",
    ),
)
_LABELED_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"(?<![A-Za-z0-9_])['\"]?(?P<field_name>{_LABELED_SECRET_FIELD_PATTERN})"
            rf"['\"]?(?![A-Za-z0-9_])\s*[:=]\s*{_SECRET_VALUE_PATTERN}",
            re.I,
        ),
        "密钥/Token",
    ),
)
_SECRET_PATTERNS = (
    _FORMAT_ONLY_SECRET_PATTERNS + _DEFAULT_SECRET_PATTERNS + _LABELED_SECRET_PATTERNS
)
_MASKED_SECRET_RE = re.compile(
    r"(?:\*{3,}|<redacted>|\[redacted\]|your[-_][a-z0-9_-]+|\$\{[^}]+\})",
    re.I,
)
_PLACEHOLDER_SECRET_RE = re.compile(
    r"^(?:\.{3}|fake|dummy|example|placeholder|unique[-_]secret|"
    r"abcdefghijklmnopqrstuvwxyz|not[-_]?a[-_]?secret|change[-_]?me|replace[-_]?me|"
    r"(?:test|testing|fixture|mock|dummy|fake|example|sample|placeholder|synthetic)"
    r"(?:[-_](?:token|key|secret|password|credential|value|data))?"
    r"(?:[-_][a-z0-9]+)*)$",
    re.I,
)
_DEFAULT_SECRET_PLACEHOLDER_RE = re.compile(
    r"^(?:none|null|nil|empty|unset|unknown|default|"
    r"not[-_ ]?(?:set|configured|provided)|"
    r"(?:no|without)[-_ ]?(?:token|key|secret))$",
    re.I,
)
_NON_PRODUCTION_SECRET_CONTEXT_RE = re.compile(
    r"\b(?:example|sample|placeholder|dummy|fake|fixture|mock|redacted|"
    r"documentation|readme|docs?|usage|guide|tutorial)\b|"
    r"示例|样例|占位|虚构|脱敏|文档|fixture",
    re.I,
)
_SOURCE_FILE_CONTEXT_RE = re.compile(
    r"(?:^|[\s`\"'(<:/\\])(?:src|app|lib|config|packages?)[/\\]"
    r"[^\s\n:]+:\d+(?::\d+)?",
    re.I,
)
_TEST_SOURCE_CONTEXT_RE = re.compile(
    r"(?:^|[\s`\"'(<:/\\])(?:tests?|__tests__|fixtures?|__fixtures__|"
    r"specs?|__snapshots__|snapshots?)[/\\]"
    r"|(?:^|[\s`\"'(<:/\\])(?:test_[A-Za-z0-9_.-]+|"
    r"[A-Za-z0-9_.-]+_test\.[A-Za-z0-9]+|"
    r"[A-Za-z0-9_.-]+\.spec\.[A-Za-z0-9]+)"
    r"|(?-i:(?:Test[^/\s]+|[^/\s]+(?:Test|Tests|Spec))\."
    r"(?:java|kt|kts|scala|groovy|cs|swift|m|mm|js|jsx|ts|tsx))"
    r"|\b(?:def|function|describe|it|test)\s*[ (:_-]*"
    r"(?:test|tests|fixture|mock|spec)\b",
    re.I,
)
_TEST_FUNCTION_CONTEXT_RE = re.compile(
    r"\b(?:def|function|describe|it|test)\s*[ (:_-]*"
    r"(?:test|tests|fixture|mock|spec)\b",
    re.I,
)
_TEST_FAILURE_CONTEXT_RE = re.compile(
    r"(?:^=+\s*(?:FAILURES|ERRORS)\s*=+$|^FAILED\b|^E\s+|"
    r"Traceback \(most recent call last\)|\bAssertionError\b|"
    r"short test summary)",
    re.I | re.M,
)
_NON_PRODUCTION_SECRET_CONTEXT_WINDOW_CHARS = 256
_SYMBOLIC_SECRET_EXPRESSION_RE = re.compile(
    r"^(?:[A-Za-z_$][A-Za-z0-9_$]*\s*(?:\.\s*[A-Za-z_$][A-Za-z0-9_$]*|\[[^\]]+\])*"
    r"\s*(?:\([^)]*\))?)$"
)
_SYMBOLIC_SECRET_WORD_RE = re.compile(
    r"(?:api[-_]?key|access[-_]?token|access[-_]?key|secret|password|credential|"
    r"token|value|input|config|settings|state|env|process|args|self)",
    re.I,
)
_KNOWN_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{19,}", re.I),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}", re.I),
    re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}", re.I),
)
_ORDERED_SECRET_ALPHABETS = (
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
)
_WORD_LIKE_SECRET_PHRASE_RE = re.compile(r"^[A-Za-z]+(?:[-_][A-Za-z]+)+(?:[-_]\d+)?$")
_TEST_SOURCE_FILENAME_RE = re.compile(
    r"(?:test_[^/]+|[^/]+_test\.[A-Za-z0-9]+|[^/]+\.spec\.[A-Za-z0-9]+)$",
    re.I,
)
_CAMEL_CASE_TEST_SOURCE_FILENAME_RE = re.compile(
    r"(?:Test[^/]+|[^/]+(?:Test|Tests|Spec))\."
    r"(?:java|kt|kts|scala|groovy|cs|swift|m|mm|js|jsx|ts|tsx)$"
)
_TEST_SOURCE_DIRECTORY_RE = re.compile(
    r"(?:^|/)(?:__tests__|tests?|__fixtures__|fixtures?|__snapshots__|snapshots?|specs?)(?:/|$)",
    re.I,
)
_NON_PRODUCTION_SOURCE_DIRECTORY_RE = re.compile(
    r"(?:^|/)(?:docs?|documentation|examples?)(?:/|$)",
    re.I,
)
_SOURCE_INSPECTION_COMMAND_RE = re.compile(
    r"\b(?:cat|sed|nl|head|tail|less|more|awk|rg|grep)\b|"
    r"\bgit\s+(?:diff|show|blame|grep)\b",
    re.I,
)
_SOURCE_PATH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:/[^\s\"'`;&|]+/)?"
    r"(?:src|app|lib|tests?|__tests__|fixtures?|__fixtures__|"
    r"specs?|__snapshots__|snapshots?|docs?|documentation|examples?)"
    r"(?:/[^\s\"'`;&|]+)?",
    re.I,
)
_DIFF_SOURCE_HEADER_RE = re.compile(
    r"diff --git a/(?P<old>\S+) b/(?P<new>\S+)",
)
_STRICT_GENERIC_SECRET_MIN_LENGTH = 20
_STRICT_GENERIC_SECRET_MIN_CHAR_CLASSES = 3


def _valid_basic_credentials(value: str) -> bool:
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (UnicodeDecodeError, ValueError, binascii.Error):
        return False
    username, separator, password = decoded.partition(":")
    return bool(separator and username and password)


def is_test_source_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    filename = normalized.rsplit("/", 1)[-1]
    return (
        _TEST_SOURCE_FILENAME_RE.fullmatch(filename) is not None
        or _CAMEL_CASE_TEST_SOURCE_FILENAME_RE.fullmatch(filename) is not None
        or _TEST_SOURCE_DIRECTORY_RE.search(normalized) is not None
    )


def is_non_production_source_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        is_test_source_path(normalized)
        or _NON_PRODUCTION_SOURCE_DIRECTORY_RE.search(normalized) is not None
    )


def _command_source_paths(command: str) -> tuple[str, ...]:
    paths: list[str] = []
    for match in _SOURCE_PATH_TOKEN_RE.finditer(command):
        candidate = match.group(0).rstrip('.,:;)]}"')
        if candidate and candidate not in paths:
            paths.append(candidate)
    return tuple(paths)


def is_non_production_source_command(command: str) -> bool:
    if not command or _SOURCE_INSPECTION_COMMAND_RE.search(command) is None:
        return False
    paths = _command_source_paths(command)
    return bool(paths) and all(is_non_production_source_path(path) for path in paths)


def is_test_source_command(command: str) -> bool:
    """Return whether a command mentions a test source path."""
    if not command:
        return False
    return any(is_test_source_path(path) for path in _command_source_paths(command))


def _diff_source_path(text: str, position: int) -> str | None:
    headers = list(_DIFF_SOURCE_HEADER_RE.finditer(text, 0, position))
    if not headers:
        return None
    header = headers[-1]
    return header.group("new") or header.group("old")


_TEST_SOURCE_IGNORED_PATHS = frozenset({"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr"})


def _relevant_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        path for path in paths if path.replace("\\", "/").lower() not in _TEST_SOURCE_IGNORED_PATHS
    )


def is_test_source_paths(paths: tuple[str, ...]) -> bool:
    relevant_paths = _relevant_paths(paths)
    return bool(relevant_paths) and all(is_test_source_path(path) for path in relevant_paths)


def _secret_literal_candidate(match: re.Match[str]) -> tuple[str, bool] | None:
    value = match.groupdict().get("secret_value")
    if value is None or _MASKED_SECRET_RE.search(value):
        return None
    raw = value.strip().replace('\\"', '"').replace("\\'", "'")
    raw = raw.split("\\n", 1)[0].split("\\r", 1)[0]
    quoted = len(raw) >= 2 and raw[0] in "'\"`" and raw[-1] == raw[0]
    candidate = raw.strip("'\"`").rstrip("}] ").strip("'\"`")
    if not candidate or _MASKED_SECRET_RE.search(candidate):
        return None
    if _PLACEHOLDER_SECRET_RE.fullmatch(candidate):
        return None
    if re.fullmatch(r"Optional\[[A-Za-z_][A-Za-z0-9_.]*]", candidate):
        return None
    if re.fullmatch(r"/.+/[a-z]*", candidate, re.I):
        return None
    return candidate, quoted


def _is_symbolic_secret_reference(candidate: str, *, quoted: bool = False) -> bool:
    if "$" in candidate or any(symbol in candidate for symbol in ".[]()"):
        return True
    if re.search(r"\b(?:args|self|config|settings|state|env|process|os)\.", candidate, re.I):
        return True
    if not _SYMBOLIC_SECRET_EXPRESSION_RE.fullmatch(candidate):
        return False
    if _SYMBOLIC_SECRET_WORD_RE.search(candidate):
        return not quoted
    if len(candidate) < 16:
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_]+", candidate):
        return not quoted
    return False


def _normalize_context_fragment(value: str) -> str:
    return value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\r")


def _normalized_context_range(source: str, start: int, end: int) -> tuple[str, int, int]:
    normalized = _normalize_context_fragment(source)
    if normalized == source:
        return source, start, end
    normalized_start = len(_normalize_context_fragment(source[:start]))
    normalized_end = len(_normalize_context_fragment(source[:end]))
    return normalized, normalized_start, normalized_end


def _has_symbolic_secret_syntax(candidate: str) -> bool:
    return "$" in candidate or any(symbol in candidate for symbol in ".[]()")


def _has_non_production_secret_context(
    text: str,
    match: re.Match[str],
    *,
    context_text: str | None = None,
    context_offset: int = 0,
) -> bool:
    source = context_text or text
    match_start = match.start() + context_offset if context_text is not None else match.start()
    match_end = match.end() + context_offset if context_text is not None else match.end()
    start = max(0, match_start - _NON_PRODUCTION_SECRET_CONTEXT_WINDOW_CHARS)
    end = min(len(source), match_end + _NON_PRODUCTION_SECRET_CONTEXT_WINDOW_CHARS)
    if _NON_PRODUCTION_SECRET_CONTEXT_RE.search(source[start:end]) is not None:
        return True
    line_source, line_match_start, line_match_end = _normalized_context_range(
        source, match_start, match_end
    )
    line_start = line_source.rfind("\n", 0, line_match_start) + 1
    line_end = line_source.find("\n", line_match_end)
    if line_end < 0:
        line_end = len(line_source)
    for _ in range(8):
        previous = line_source.rfind("\n", 0, max(0, line_start - 1))
        if previous < 0:
            line_start = 0
            break
        line_start = previous + 1
    for _ in range(8):
        following = line_source.find("\n", line_end + 1)
        if following < 0:
            line_end = len(line_source)
            break
        line_end = following
    nearby_lines = line_source[line_start:line_end]
    current_line_end = line_source.find("\n", line_match_end)
    if current_line_end < 0:
        current_line_end = len(line_source)
    current_line = line_source[line_source.rfind("\n", 0, line_match_start) + 1 : current_line_end]
    if _TEST_SOURCE_CONTEXT_RE.search(current_line) is not None:
        return True
    if (
        _SOURCE_FILE_CONTEXT_RE.search(current_line) is None
        and _TEST_SOURCE_CONTEXT_RE.search(nearby_lines) is not None
    ):
        return True
    if (
        _SOURCE_FILE_CONTEXT_RE.search(current_line) is None
        and _NON_PRODUCTION_SOURCE_DIRECTORY_RE.search(nearby_lines) is not None
    ):
        return True
    diff_path = _diff_source_path(line_source, line_match_start)
    if diff_path and is_non_production_source_path(diff_path):
        return True
    if _TEST_FUNCTION_CONTEXT_RE.search(nearby_lines) is not None:
        return True
    if _TEST_FAILURE_CONTEXT_RE.search(nearby_lines) is not None:
        return True
    fence_start = source.rfind("```", 0, match_start)
    fence_end = source.find("```", match_end)
    if fence_start < 0 or fence_end < 0:
        return False
    return _NON_PRODUCTION_SECRET_CONTEXT_RE.search(source[fence_start : fence_end + 3]) is not None


def _is_explicit_default_secret_candidate(candidate: str) -> bool:
    if (
        len(candidate) < 12
        or _MASKED_SECRET_RE.search(candidate)
        or _PLACEHOLDER_SECRET_RE.fullmatch(candidate)
        or _DEFAULT_SECRET_PLACEHOLDER_RE.fullmatch(candidate)
        or _has_symbolic_secret_syntax(candidate)
    ):
        return False
    if re.fullmatch(r"[A-Z][A-Z0-9_]+", candidate):
        return False
    return True


def _has_known_secret_value_format(candidate: str) -> bool:
    return any(pattern.fullmatch(candidate) for pattern in _KNOWN_SECRET_VALUE_PATTERNS)


def _has_ordered_secret_run(candidate: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", candidate.lower())
    if len(compact) < 6:
        return False
    for alphabet in _ORDERED_SECRET_ALPHABETS:
        reverse = alphabet[::-1]
        for start in range(len(compact) - 5):
            segment = compact[start : start + 6]
            if segment in alphabet or segment in reverse:
                return True
    return bool(re.search(r"(.{1,4})\1{3,}", compact))


def _secret_entropy(candidate: str) -> float:
    counts = {char: candidate.count(char) for char in set(candidate)}
    length = len(candidate)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _has_high_entropy_secret_shape(
    candidate: str,
    *,
    min_length: int = 16,
    min_char_classes: int = 2,
) -> bool:
    if (
        len(candidate) < min_length
        or _has_ordered_secret_run(candidate)
        or _WORD_LIKE_SECRET_PHRASE_RE.fullmatch(candidate)
    ):
        return False
    unique_ratio = len(set(candidate)) / len(candidate)
    if unique_ratio < 0.45:
        return False
    char_classes = sum(
        bool(pattern.search(candidate))
        for pattern in (
            re.compile(r"[a-z]"),
            re.compile(r"[A-Z]"),
            re.compile(r"\d"),
            re.compile(r"[^A-Za-z0-9]"),
        )
    )
    if char_classes < min_char_classes:
        return False
    entropy = _secret_entropy(candidate)
    return entropy >= 3.0


def _normalized_secret_field(field_name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (field_name or "").lower())


def _has_secret_field_companion(text: str, match: re.Match[str], field_name: str | None) -> bool:
    normalized = _normalized_secret_field(field_name)
    if normalized == "ak":
        companion = r"sk"
    elif normalized == "sk":
        companion = r"ak"
    elif normalized == "accesskeyid":
        companion = r"access[_-]?key[_-]?secret|secret[_-]?access[_-]?key"
    elif normalized in {"accesskeysecret", "secretaccesskey"}:
        companion = r"access[_-]?key[_-]?id"
    else:
        return True
    start = max(0, match.start() - 512)
    end = min(len(text), match.end() + 512)
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_])['\"]?(?:{companion})['\"]?"
        r"(?![A-Za-z0-9_])\s*[:=]",
        re.I,
    )
    return pattern.search(text[start:end]) is not None


def _looks_like_secret_literal(
    match: re.Match[str],
    category: str,
    *,
    test_source: bool = False,
    context_text: str | None = None,
    context_offset: int = 0,
) -> bool:
    if test_source:
        return False
    if category in {
        "私钥",
        "Bearer Token",
        "Basic 凭据",
        "数据库连接凭据",
        "访问密钥",
        "密钥/Token",
    } and _has_non_production_secret_context(
        match.string,
        match,
        context_text=context_text,
        context_offset=context_offset,
    ):
        return False
    value = match.groupdict().get("secret_value")
    if category == "Basic 凭据":
        return value is not None and _valid_basic_credentials(value)
    if value is None:
        whole = match.group(0)
        if _MASKED_SECRET_RE.search(whole):
            return False
        if whole.lower().startswith("bearer "):
            token = whole.split(None, 1)[1]
            return _PLACEHOLDER_SECRET_RE.fullmatch(token) is None
        return True

    literal = _secret_literal_candidate(match)
    if literal is None:
        return False
    candidate, quoted = literal
    if category == "数据库连接凭据":
        return len(candidate) >= 3
    if category == "密钥/Token":
        if _has_known_secret_value_format(candidate):
            return True
        if match.groupdict().get("default_marker"):
            return _is_explicit_default_secret_candidate(candidate)
        if (
            not quoted
            or _is_symbolic_secret_reference(candidate, quoted=quoted)
            or not _has_secret_field_companion(
                match.string, match, match.groupdict().get("field_name")
            )
        ):
            return False
        return _has_high_entropy_secret_shape(
            candidate,
            min_length=_STRICT_GENERIC_SECRET_MIN_LENGTH,
            min_char_classes=_STRICT_GENERIC_SECRET_MIN_CHAR_CLASSES,
        )
    return True


def _secret_finding(text: str) -> str | None:
    for pattern, label in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if _looks_like_secret_literal(match, label):
                return label
    return None


_COMMAND_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?P<local>[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]+)@"
            r"(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b"
        ),
        "邮箱地址",
    ),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "手机号码"),
    (
        re.compile(
            r"(?<![0-9A-Za-z])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9A-Za-z])"
        ),
        "居民身份证",
    ),
)


@dataclass(frozen=True)
class SafetyFinding:
    category: str
    excerpt: str


def _masked_sensitive_excerpt(category: str, match: re.Match[str]) -> str:
    raw = match.group(0)
    value = match.groupdict().get("secret_value")
    if value is not None:
        start, end = match.span("secret_value")
        return raw[: start - match.start()] + "****" + raw[end - match.start() :]
    if category == "私钥":
        return "-----BEGIN ... PRIVATE KEY----- [REDACTED]"
    if category == "Bearer Token":
        return "Bearer ****"
    if category == "邮箱地址":
        local, domain = raw.rsplit("@", 1)
        return f"{local[:1]}***@{domain}"
    if category == "手机号码":
        return raw[:3] + "****" + raw[-4:]
    if category in {"居民身份证", "银行卡"}:
        compact = re.sub(r"[ -]", "", raw)
        return compact[:6] + "****" + compact[-4:]
    return f"{category}：****"


def _iter_sensitive_matches(
    text: str,
    *,
    test_source: bool = False,
    context_text: str | None = None,
    context_offset: int = 0,
) -> Iterator[tuple[str, re.Match[str]]]:
    if test_source:
        return
    for pattern, category in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if _looks_like_secret_literal(
                match,
                category,
                test_source=test_source,
                context_text=context_text,
                context_offset=context_offset,
            ):
                yield category, match


def find_first_sensitive_finding(
    text: str,
    *,
    test_source: bool = False,
    context_text: str | None = None,
    context_offset: int = 0,
) -> SafetyFinding | None:
    for category, match in _iter_sensitive_matches(
        text,
        test_source=test_source,
        context_text=context_text,
        context_offset=context_offset,
    ):
        return SafetyFinding(category, _masked_sensitive_excerpt(category, match))
    return None


def find_sensitive_findings(
    text: str,
    *,
    max_findings: int | None = MAX_SAFETY_FINDINGS,
    test_source: bool = False,
) -> list[SafetyFinding]:
    """Find credential-like strong signals, excluding generic personal PII."""
    findings: list[SafetyFinding] = []
    seen: set[tuple[str, str]] = set()
    seen_ranges: list[tuple[int, int]] = []

    def add(category: str, match: re.Match[str]) -> None:
        if max_findings is not None and len(findings) >= max_findings:
            return
        start, end = match.span()
        if any(
            start < previous_end and previous_start < end
            for previous_start, previous_end in seen_ranges
        ):
            return
        finding = SafetyFinding(category, _masked_sensitive_excerpt(category, match))
        key = (finding.category, finding.excerpt)
        if key not in seen:
            findings.append(finding)
            seen.add(key)
            seen_ranges.append((start, end))

    for category, match in _iter_sensitive_matches(
        text,
        test_source=test_source,
    ):
        add(category, match)
        if max_findings is not None and len(findings) >= max_findings:
            return findings
    return findings


_DATABASE_CLIENT_RE = re.compile(r"^(?:mysql|psql|sqlite3|duckdb|sqlcmd|clickhouse-client)\b", re.I)
_HEREDOC_START_RE = re.compile(r"<<-?\s*['\"]?(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)['\"]?")
_ENV_PREFIX_RE = re.compile(r"^(?:(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]+\s+)+)")
_CODEX_HIGH_RISK_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^rm\b", re.I), "Codex 禁止直接删除命令"),
    (re.compile(r"^(?:curl|wget)\b", re.I), "Codex 禁止直接网络请求"),
    (
        re.compile(
            r"^codex\b.*(?:--yolo|--dangerously-bypass-approvals-and-sandbox|"
            r"(?:--sandbox|--sandbox-mode)\s+danger-full-access)\b",
            re.I,
        ),
        "Codex 关闭审批或沙箱",
    ),
    (
        re.compile(
            r"^(?:cat|less|head|tail|vim|nano)\b.*(?:\.env(?:\.[^\s/]*)?|"
            r"\.pem\b|\.key\b|secret|credential|password)",
            re.I,
        ),
        "Codex 读取敏感凭据文件",
    ),
)


_OPENCLAW_HIGH_RISK_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^openclaw\s+config\s+set\s+gateway\.(?:bind|host)\s+(?:0\.0\.0\.0|::|all|public)\b",
            re.I,
        ),
        "OpenClaw 暴露本地控制台",
    ),
    (
        re.compile(
            r"^openclaw\s+config\s+set\s+gateway\.auth\.mode\s+(?:none|off|disabled|false)\b", re.I
        ),
        "OpenClaw 关闭控制台认证",
    ),
    (
        re.compile(
            r"^openclaw\s+config\s+set\s+.*(?:sandbox|isolation).*\s+(?:false|off|disabled)\b", re.I
        ),
        "OpenClaw 关闭沙箱隔离",
    ),
    (
        re.compile(
            r"^openclaw\s+(?:plugin|plugins|skill|skills)\s+(?:install|add)\b.*(?:https?://|github\.com|npmjs\.com|clawhub)",
            re.I,
        ),
        "OpenClaw 安装公网第三方扩展",
    ),
    (
        re.compile(
            r"^(?:cat|less|head|tail|vim|nano)\b.*(?:\.openclaw/(?:credentials|memory)|\.env(?:\.[^\s/]*)?|prod-secrets|~?/.ssh|~?/.aws|~?/.kube)",
            re.I,
        ),
        "OpenClaw 读取敏感凭据或记忆文件",
    ),
)

_HIGH_RISK_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^mkfs(?:\.[a-z0-9]+)?\b", re.I), "破坏磁盘或文件系统"),
    (
        re.compile(r"^(?:fdisk|parted)\b(?![^\n]*(?:\s(?:-l|--list|print)\b))", re.I),
        "破坏磁盘或文件系统",
    ),
    (re.compile(r"^dd\b[^\n]*\bof=/dev/", re.I), "覆盖块设备"),
    (
        re.compile(
            r"^(?:curl|wget)\b[^\n;]*\|\s*"
            r"(?:(?:sudo|env|command|exec)\s+)*(?:sh|bash|zsh)(?:\s|$)",
            re.I,
        ),
        "下载并执行远程脚本",
    ),
    (re.compile(r"^(?:ssh|scp|rsync)\b", re.I), "未经受控的远程访问或传输"),
    (re.compile(r"^(?:ngrok|frps|frpc|lt|localtunnel)\b", re.I), "暴露本地服务或反向代理"),
    (re.compile(r"^(?:nc|netcat)\b", re.I), "未经受控的网络监听或连接"),
    (re.compile(r"^(?:mount|umount)\b", re.I), "挂载或卸载系统文件系统"),
    (
        re.compile(r"^git\s+push\b[^\n]*(?:--force(?:-with-lease)?|\s-f(?:\s|$))", re.I),
        "强制推送代码",
    ),
    (re.compile(r"^git\s+reset\s+--hard\b", re.I), "强制丢弃本地修改"),
    (re.compile(r"^chown\s+-R\b", re.I), "递归变更文件所有权"),
    (
        re.compile(r"^(?:kubectl|oc)\s+delete\b[^\n]*\s--all(?:[=\s]|$)", re.I),
        "删除集群关键资源",
    ),
    (
        re.compile(
            r"^(?:kubectl|oc)\s+delete\s+(?:namespace|ns|clusterrole(?:binding)?|crd|customresourcedefinition)\b",
            re.I,
        ),
        "删除集群关键资源",
    ),
    (
        re.compile(r"^docker\s+(?:system|volume|builder)\s+prune\b", re.I),
        "清理 Docker 关键资源",
    ),
    (re.compile(r"^terraform\s+destroy\b", re.I), "销毁基础设施"),
    (re.compile(r"^runas\b", re.I), "Windows 提权命令"),
    (
        re.compile(r"^net\s+(?:user|localgroup\s+administrators)\b", re.I),
        "Windows 账户或管理员组变更",
    ),
    (re.compile(r"^icacls\b", re.I), "Windows 文件权限变更"),
    (re.compile(r"^rmdir\s+/s\s+/q\b", re.I), "递归删除目录"),
    (re.compile(r"^del\s+/s\s+/q\s+(?:[A-Za-z]:)?", re.I), "递归删除文件"),
    (re.compile(r"^format\b", re.I), "格式化磁盘"),
    (re.compile(r"^(?:regedit|reg\s+add)\b", re.I), "Windows 注册表变更"),
    (
        re.compile(r"^powershell(?:\.exe)?\b.*\bstart-process\b.*\b-verb\s+runas\b", re.I),
        "Windows 提权命令",
    ),
)


def _strip_heredoc_bodies(command: str) -> str:
    """Drop embedded program text before inspecting shell command segments."""
    lines: list[str] = []
    delimiter: str | None = None
    for line in command.splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        lines.append(line)
        if match := _HEREDOC_START_RE.search(line):
            delimiter = match.group("delimiter")
    return "\n".join(lines)


def _split_shell_segments(command: str, *, split_pipe: bool = False) -> list[str]:
    """Split shell operators while preserving quoted strings."""
    source = _strip_heredoc_bodies(command)
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0

    def flush() -> None:
        segment = "".join(current).strip()
        if segment:
            segments.append(segment)
        current.clear()

    while index < len(source):
        char = source[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            index += 1
            continue
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote = char
            index += 1
            continue
        if char in "\r\n;":
            flush()
            index += 1
            continue
        if source.startswith("&&", index) or source.startswith("||", index):
            flush()
            index += 2
            continue
        if split_pipe and char == "|":
            flush()
            index += 1
            continue
        current.append(char)
        index += 1
    flush()
    return segments


def _iter_shell_segments(command: str):
    """Yield top-level shell segments, not strings embedded in scripts or docs."""
    for segment in _split_shell_segments(command):
        normalized = _ENV_PREFIX_RE.sub("", segment.strip()).strip()
        if normalized:
            yield normalized


_SHELL_COMMAND_NAMES = {"dash", "bash", "fish", "ksh", "sh", "su", "zsh"}
_LEADING_WRAPPERS = {"command", "exec", "time"}
_SUDO_OPTION_ARGUMENTS = {"-C", "-R", "-g", "-h", "-p", "-r", "-u", "--chdir", "--group", "--user"}
_CODE_FRAGMENT_PREFIX_RE = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\s*(?:=|=>|:|\()"
)


_SAFETY_RULE_PROBE_MARKER_RE = re.compile(
    r"\b(?:find_(?:first_)?sensitive_finding|find_sensitive_findings|"
    r"_iter_sensitive_matches|_format_only_secret_patterns|"
    r"SensitiveLeakageExecutor|sensitive_leakage|SafetyFinding)\b",
    re.I,
)
_SCRIPT_COMMAND_RE = re.compile(r"(?:^|[\s/])(?:python(?:3)?|node|perl|ruby)\b", re.I)


def _token_name(token: str) -> str:
    return token.rsplit("/", 1)[-1].lower()


def _shell_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return [token for token in re.split(r"\s+", command.strip()) if token]


def _strip_leading_wrappers(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens):
        name = _token_name(tokens[index])
        if name in _LEADING_WRAPPERS:
            index += 1
            continue
        if name == "env":
            index += 1
            while index < len(tokens):
                token = tokens[index]
                if token == "--":
                    index += 1
                    break
                if token in {"-i", "--ignore-environment"}:
                    index += 1
                elif token in {"-u", "--unset"}:
                    index += 2
                elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
                    index += 1
                else:
                    break
            continue
        if name == "sudo":
            index += 1
            while index < len(tokens):
                token = tokens[index]
                if token == "--":
                    index += 1
                    break
                if token in _SUDO_OPTION_ARGUMENTS:
                    index += 2
                elif token.startswith(("--chdir=", "--group=", "--user=")):
                    index += 1
                elif token.startswith("-"):
                    index += 1
                else:
                    break
            continue
        break
    return tokens[index:]


def _shell_command_payload(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens):
        if _token_name(token) not in _SHELL_COMMAND_NAMES:
            continue
        for option_index in range(index + 1, len(tokens)):
            option = tokens[option_index]
            if option == "--":
                break
            is_command_option = option in {"-c", "--command"} or (
                option.startswith("-") and not option.startswith("--") and "c" in option[1:]
            )
            if is_command_option and option_index + 1 < len(tokens):
                return tokens[option_index + 1]
    return None


def _is_code_fragment(command: str) -> bool:
    """Reject common source-code lines before matching shell command rules."""
    return _CODE_FRAGMENT_PREFIX_RE.match(command.strip()) is not None


def _iter_command_variants(command: str):
    """Yield commands after conservatively unwrapping shell launchers."""
    queue = [command]
    seen: set[str] = set()
    while queue:
        current = queue.pop(0).strip()
        if not current or current in seen:
            continue
        seen.add(current)
        yield current
        tokens = _shell_tokens(current)
        if not tokens:
            continue
        stripped = _strip_leading_wrappers(tokens)
        if stripped and stripped != tokens:
            queue.append(shlex.join(stripped))
        payload = _shell_command_payload(tokens)
        if payload:
            queue.extend(_split_shell_segments(payload))


_SENSITIVE_FILE_SUFFIXES = (".pem", ".key")
_SENSITIVE_FILE_BASENAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
_SENSITIVE_FILE_DIRECTORIES = {".ssh", ".aws", ".aliyun", ".azure", ".kube"}
_ENV_TEMPLATE_NAMES = {".env.example", ".env.sample", ".env.template", ".env.dist"}
_HTTP_URL_RE = re.compile(r"^https?://", re.I)
_SENSITIVE_FILE_READER_RE = re.compile(r"\b(?:cat|base64|tar|zip|gzip)\b(?P<args>[^|;&\n]*)", re.I)
_SCRIPT_SENSITIVE_FILE_ACCESS_RE = re.compile(
    r"\b(?:open|Path)\s*\(\s*(?:r|u|b|f)?(?P<quote>['\"])(?P<path>[^'\"]+)\1",
    re.I,
)
_TRANSFER_COMMAND_NAMES = frozenset({"curl", "wget", "scp", "rsync", "nc", "netcat"})
_CURL_UPLOAD_OPTIONS = {
    "-d",
    "--data",
    "--data-ascii",
    "--data-binary",
    "--data-raw",
    "-F",
    "--form",
    "-T",
    "--upload-file",
}
_CURL_UPLOAD_OPTION_PREFIXES = (
    "--data=",
    "--data-ascii=",
    "--data-binary=",
    "--data-raw=",
    "--form=",
    "--upload-file=",
)
_FILE_OPTION_NAMES = {"-T", "--upload-file", "--post-file"}
_OPTION_VALUE_FLAGS = {
    "-e",
    "-P",
    "-S",
    "--acl",
    "--endpoint-url",
    "--exclude",
    "--exclude-from",
    "--files-from",
    "--include",
    "--profile",
    "--region",
    "--rsh",
    "--storage-class",
}


def _is_sensitive_file_reference(value: str) -> bool:
    candidate = value.strip().strip("'\"`),;:").lstrip("@")
    candidate = candidate.removeprefix("file://").rstrip(")]}'\"")
    if not candidate or _HTTP_URL_RE.match(candidate):
        return False
    path = candidate.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    if name == ".env" or (name.startswith(".env.") and name not in _ENV_TEMPLATE_NAMES):
        return True
    if name in _SENSITIVE_FILE_BASENAMES or name.endswith(_SENSITIVE_FILE_SUFFIXES):
        return not name.endswith(".pub")
    parts = [part for part in path.split("/") if part not in {"", ".", "~"}]
    if any(part in _SENSITIVE_FILE_DIRECTORIES for part in parts):
        return True
    return any(parts[index : index + 2] == [".config", "gcloud"] for index in range(len(parts)))


def is_safety_rule_probe_command(command: str) -> bool:
    """Return whether a script is inspecting or exercising this safety rule."""
    if not command:
        return False
    return bool(
        _SCRIPT_COMMAND_RE.search(command)
        and _SAFETY_RULE_PROBE_MARKER_RE.search(command)
        and ("<<" in command or "-c" in command or "-e" in command)
    )


def is_sensitive_file_path(value: str) -> bool:
    """Return whether a structured path points to a credential-bearing file."""
    return _is_sensitive_file_reference(value)


def _has_sensitive_file_reader(command: str) -> bool:
    for match in _SENSITIVE_FILE_READER_RE.finditer(command):
        for token in _shell_tokens(match.group("args")):
            if token != "--" and not token.startswith("-") and _is_sensitive_file_reference(token):
                return True
    return False


def _has_direct_sensitive_file_reference(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token in _FILE_OPTION_NAMES and index + 1 < len(tokens):
            if _is_sensitive_file_reference(tokens[index + 1]):
                return True
        if token.startswith(("--upload-file=", "--post-file=")):
            _, value = token.split("=", 1)
            if _is_sensitive_file_reference(value):
                return True
        for value in re.findall(r"@([^\s,'\"|;]+)", token):
            if _is_sensitive_file_reference(value):
                return True
    return False


def _is_http_upload_command(tokens: list[str]) -> bool:
    if not tokens or _token_name(tokens[0]) not in {"curl", "wget"}:
        return False
    if not any(_HTTP_URL_RE.match(token) for token in tokens[1:]):
        return False
    if _token_name(tokens[0]) == "wget":
        return any(
            token == "--post-file" or token.startswith("--post-file=") for token in tokens[1:]
        )
    return any(
        token in _CURL_UPLOAD_OPTIONS
        or token.startswith(_CURL_UPLOAD_OPTION_PREFIXES)
        or token.startswith(("-d", "-F", "-T"))
        for token in tokens[1:]
    )


def _is_option_value(tokens: list[str], index: int) -> bool:
    return index > 0 and tokens[index - 1] in _OPTION_VALUE_FLAGS


def _has_sensitive_file_before(tokens: list[str], destination_index: int) -> bool:
    return any(
        not token.startswith("-")
        and not _is_option_value(tokens, index)
        and _is_sensitive_file_reference(token)
        for index, token in enumerate(tokens[:destination_index])
    )


def _is_remote_destination(token: str) -> bool:
    return bool(re.fullmatch(r"(?:[^@:/\s]+@)?[^:/\s]+:.+", token))


def _is_file_transfer_command(tokens: list[str]) -> bool:
    if not tokens or _token_name(tokens[0]) not in {"scp", "rsync"}:
        return False
    destination_indexes = [
        index for index, token in enumerate(tokens[1:], start=1) if _is_remote_destination(token)
    ]
    if not destination_indexes:
        return False
    return _has_sensitive_file_before(tokens, destination_indexes[-1])


def _is_object_storage_upload(tokens: list[str]) -> bool:
    if not tokens:
        return False
    name = _token_name(tokens[0])
    if name == "aws" and tokens[1:3] in (["s3", "cp"], ["s3", "sync"]):
        destination_prefixes = ("s3://",)
    elif name == "ossutil" and tokens[1:2] == ["cp"]:
        destination_prefixes = ("oss://",)
    elif name == "gsutil" and tokens[1:2] == ["cp"]:
        destination_prefixes = ("gs://",)
    else:
        return False
    destination_indexes = [
        index
        for index, token in enumerate(tokens)
        if token.lower().startswith(destination_prefixes)
    ]
    return bool(destination_indexes) and _has_sensitive_file_before(tokens, destination_indexes[-1])


def _is_sensitive_file_exfiltration(command: str) -> bool:
    for variant in _iter_command_variants(command):
        readers_before = False
        for part in _split_shell_segments(variant, split_pipe=True):
            tokens = _strip_leading_wrappers(_shell_tokens(part))
            if not tokens:
                continue
            if _is_http_upload_command(tokens) and (
                readers_before
                or _has_sensitive_file_reader(part)
                or _has_direct_sensitive_file_reference(tokens)
            ):
                return True
            if _is_file_transfer_command(tokens) or _is_object_storage_upload(tokens):
                return True
            readers_before = readers_before or _has_sensitive_file_reader(part)
    return False


def is_sensitive_file_access_or_transfer_command(command: str) -> bool:
    """Detect actual sensitive-file reads or transfer commands in shell/script input."""
    if not command:
        return False
    if _is_sensitive_file_exfiltration(command):
        return True
    if any(
        _is_sensitive_file_reference(match.group("path"))
        for match in _SCRIPT_SENSITIVE_FILE_ACCESS_RE.finditer(command)
    ):
        return True
    for variant in _iter_command_variants(command):
        for part in _split_shell_segments(variant, split_pipe=True):
            normalized = _ENV_PREFIX_RE.sub("", part.strip()).strip()
            tokens = _strip_leading_wrappers(_shell_tokens(normalized))
            if _has_sensitive_file_reader(normalized):
                return True
            if tokens and _token_name(tokens[0]) in _TRANSFER_COMMAND_NAMES:
                return True
    return False


def _is_root_delete(command: str) -> bool:
    tokens = _shell_tokens(command)
    if not tokens or _token_name(tokens[0]) != "rm":
        return False
    recursive = False
    options_done = False
    paths: list[str] = []
    for token in tokens[1:]:
        if not options_done and token == "--":
            options_done = True
            continue
        if not options_done and token.startswith("-") and token != "-":
            if token.startswith("--"):
                recursive = recursive or token == "--recursive"
            else:
                recursive = recursive or any(flag in token.lower() for flag in "r")
            continue
        paths.append(token)
    return recursive and any(path in {"/", "~"} or path.startswith("/*") for path in paths)


def _is_high_risk_chmod(command: str) -> bool:
    tokens = _shell_tokens(command)
    if not tokens or _token_name(tokens[0]) != "chmod":
        return False
    mode = next(
        (token for token in tokens[1:] if token != "--" and not token.startswith("-")),
        None,
    )
    if mode is None:
        return False
    if re.fullmatch(r"[0-7]{3,4}", mode):
        return (len(mode) == 4 and mode[0] != "0") or mode[-1] in "2367"
    for clause in mode.split(","):
        match = re.fullmatch(r"(?P<who>[ugoa]*)(?P<op>[+=-])(?P<perms>[rwxXstugo]+)", clause)
        if not match:
            continue
        who = match.group("who")
        applies_to_other = not who or "a" in who or "o" in who
        perms = match.group("perms")
        if applies_to_other and match.group("op") in "+=" and "w" in perms:
            return True
        if "s" in perms and (not who or "a" in who or "u" in who):
            return True
    return False


def _is_explicit_privilege_shell(command: str) -> bool:
    tokens = _shell_tokens(command)
    if not tokens:
        return False
    for index, token in enumerate(tokens):
        name = _token_name(token)
        if name == "su":
            return True
        if name == "sudo":
            rest = tokens[index + 1 :]
            if any(option in {"-i", "-s", "--login", "--shell"} for option in rest):
                return True
            if any(_token_name(item) in _SHELL_COMMAND_NAMES - {"su"} for item in rest):
                return True
    return False


def _classify_command(
    command: str, patterns: tuple[tuple[re.Pattern[str], str], ...]
) -> str | None:
    for variant in _iter_command_variants(command):
        if _is_code_fragment(variant):
            continue
        if _is_root_delete(variant):
            return "递归删除根目录或用户目录"
        if _is_high_risk_chmod(variant):
            return "放宽文件权限"
        category = next(
            (label for pattern, label in patterns if pattern.search(variant)),
            None,
        )
        if category is not None:
            return category
        if _DATABASE_CLIENT_RE.search(variant):
            if re.search(r"\bdrop\s+(?:table|database|schema)\b", variant, re.I):
                return "删除数据库对象"
            if re.search(r"\btruncate\s+table\b", variant, re.I):
                return "清空数据表"
            if re.search(r"\bdelete\s+from\b", variant, re.I) and not re.search(
                r"\bwhere\b", variant, re.I
            ):
                return "无条件删除数据"
    if _is_explicit_privilege_shell(command):
        return "提权命令"
    return None


def _redact_sensitive_pattern(pattern: re.Pattern[str], category: str, text: str) -> str:
    def redact(match: re.Match[str]) -> str:
        return _masked_sensitive_excerpt(category, match)

    return pattern.sub(redact, text)


def _safe_command_excerpt(command: str) -> str:
    redacted = command
    for pattern, category in _SECRET_PATTERNS:
        redacted = _redact_sensitive_pattern(pattern, category, redacted)
    # Keep command evidence redacted without making personal PII a finding.
    for pattern, category in _COMMAND_REDACTION_PATTERNS:
        redacted = _redact_sensitive_pattern(pattern, category, redacted)
    return redacted[:160]


def find_high_risk_commands(
    command: str,
    *,
    max_findings: int | None = MAX_SAFETY_FINDINGS,
) -> list[SafetyFinding]:
    """Return every confirmed high-risk top-level shell segment.

    The rules are runtime-independent.  Explicit risks that originally came
    from product-specific policies are part of this shared safety baseline;
    ordinary workspace cleanup and ordinary network fetches are not.
    """
    findings: list[SafetyFinding] = []
    patterns = (
        *_CODEX_HIGH_RISK_COMMAND_PATTERNS[2:],
        *_OPENCLAW_HIGH_RISK_COMMAND_PATTERNS,
        *_HIGH_RISK_COMMAND_PATTERNS,
    )
    for segment in _iter_shell_segments(command):
        pipeline_segments = [segment]
        if _is_sensitive_file_exfiltration(segment):
            findings.append(SafetyFinding("敏感文件外传", _safe_command_excerpt(segment)))
            if max_findings is not None and len(findings) >= max_findings:
                return findings
            continue
        pipeline_segments.extend(
            part for part in _split_shell_segments(segment, split_pipe=True) if part != segment
        )
        category = next(
            (
                matched
                for candidate in pipeline_segments
                if (matched := _classify_command(candidate, patterns)) is not None
            ),
            None,
        )
        if category is not None:
            findings.append(SafetyFinding(category, _safe_command_excerpt(segment)))
            if max_findings is not None and len(findings) >= max_findings:
                return findings
    return findings
