"""Organization-file bootstrap shared by source and native entry points."""

import os
import sys
from pathlib import Path


def read_org_id(path: Path) -> str:
    try:
        value = path.read_text().strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("agentlens: cannot read SIGNOZ_ORG_ID_FILE") from exc
    if not value or any(character.isspace() for character in value):
        raise ValueError("agentlens: SIGNOZ_ORG_ID_FILE must contain one nonempty organization ID")
    return value


def main() -> None:
    try:
        print(read_org_id(Path(os.environ["SIGNOZ_ORG_ID_FILE"])))
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
