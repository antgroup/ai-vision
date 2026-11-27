"""Small, reversible edits to the Agent's native configuration files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib


def read_object(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected a JSON object")
    return value


def atomic_write(path: Path, content: bytes, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ConfigTransaction:
    """Snapshot exact bytes before native commands or writes; restore on failure."""
    def __init__(self, *, atomic_writer=None):
        self.originals = {}
        self.removed = set()
        self.atomic_writer = atomic_writer or atomic_write

    def watch(self, path: Path):
        path = path.resolve()
        if path not in self.originals:
            self.originals[path] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None

    def write(self, path: Path, content: str, private=False):
        path = path.resolve()
        self.watch(path)
        old = self.originals[path]
        mode = 0o600 if private or old is None else old[1]
        if old is not None and old[0] == content.encode() and old[1] == mode:
            return
        self.atomic_writer(path, content.encode(), mode)

    def remove(self, path: Path):
        """Remove managed code without retaining a source backup after success."""
        if path.is_symlink():
            raise ValueError("Refusing to remove managed code through a symlink")
        path = path.resolve()
        self.watch(path)
        self.removed.add(path)
        path.unlink(missing_ok=True)

    def json(self, path, value, private=False):
        self.write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", private)

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, tb):
        if error_type:
            for path, previous in reversed(list(self.originals.items())):
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    self.atomic_writer(path, *previous)
        else:
            for path, previous in self.originals.items():
                if path not in self.removed and previous is not None and path.read_bytes() != previous[0]:
                    # Never keep raw credentials in an unprotected backup.
                    self.atomic_writer(path.with_name(path.name + ".aivision-backup"), previous[0], 0o600)
        return False


def enable_feature(content: str, feature: str) -> str:
    doc = tomllib.loads(content)
    if doc.get("features", {}).get(feature) is True:
        return content
    header = re.search(r"(?m)^\s*\[features\][ \t]*(?:#[^\n]*)?$", content)
    if header:
        next_table = re.search(r"(?m)^\s*\[", content[header.end():])
        end = header.end() + next_table.start() if next_table else len(content)
        section = content[header.end():end]
        key = re.compile(rf"(?m)^([ \t]*{re.escape(feature)}[ \t]*=[ \t]*)(?:true|false)([ \t]*(?:#[^\n]*)?)$")
        if key.search(section):
            section = key.sub(r"\g<1>true\g<2>", section)
        else:
            section = f"\n{feature} = true" + section
        result = content[:header.end()] + section.rstrip() + "\n" + content[end:]
    else:
        result = content.rstrip() + f"\n[features]\n{feature} = true\n"
    if tomllib.loads(result).get("features", {}).get(feature) is not True:
        raise ValueError("Cannot safely update the hooks feature")
    return result


def merge_hooks(doc: dict, command: str, binary: str, events: list[str]) -> dict:
    hooks = doc.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object; original configuration was preserved")
    marker = f"__aivision_{binary}__"
    for event in events:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError(f"hooks.{event} must be an array")
        kept = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"hooks.{event} contains an invalid entry")
            owned = marker in entry or any(
                isinstance(h, dict) and isinstance(h.get("command"), str)
                and Path(h["command"].strip('"\'')).name in {binary, binary + ".exe"}
                for h in entry.get("hooks", [])
            )
            if not owned:
                kept.append(entry)
        kept.append({marker: True, "matcher": ".*", "hooks": [{
            "type": "command", "command": command, "timeout": 3 if event == "SessionEnd" else 30,
        }]})
        hooks[event] = kept
    return doc


def kimi_hooks(content: str, command: str, *, include_interrupt: bool = False) -> str:
    tomllib.loads(content)
    begin, end = "# AIVISION-KIMI-BEGIN", "# AIVISION-KIMI-END"
    block = begin + "\n" + "\n".join(
        f'[[hooks]]\nevent = "{event}"\ncommand = {json.dumps(command)}\nmatcher = ""\ntimeout = 30\n'
        for event in (("Stop", "StopFailure", "Interrupt") if include_interrupt else ("Stop", "StopFailure"))
    ) + end
    if begin in content or end in content:
        if content.count(begin) != 1 or content.count(end) != 1 or content.index(begin) > content.index(end):
            raise ValueError("Incomplete managed Kimi hook block; original config preserved")
        content = content[:content.index(begin)] + block + content[content.index(end) + len(end):]
    else:
        for hook in tomllib.loads(content).get("hooks", []):
            if "kimihooks" in str(hook.get("command", "")):
                raise ValueError("Existing unmanaged kimihooks entry: remove that entry before installing")
        content = content.rstrip() + "\n" + block + "\n"
    tomllib.loads(content)
    return content
