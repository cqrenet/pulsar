#!/usr/bin/env python3
"""Print the CHANGELOG.md section for one release, for use as GitHub Release notes.

    python3 scripts/release-notes.py v3.1.3

Exits non-zero if the tag has no section, so a release cannot be published without notes.
"""

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


REPO_URL = "https://github.com/cqrenet/pulsar"


def absolute_links(markdown: str, tag: str) -> str:
    """Point relative links (DEPLOY.md, docs/security/...) at the tag on GitHub; they do not resolve on a Release page."""

    def fix(match: re.Match) -> str:
        target = match.group(2)
        if target.startswith(("http://", "https://", "#", "mailto:")):
            return match.group(0)
        return f"]({REPO_URL}/blob/{tag}/{target})"

    return re.sub(r"(\]\()([^)\s]+)\)", lambda m: fix(m), markdown)


def section(text: str, version: str) -> str:
    heading = re.compile(rf"^## \[{re.escape(version)}\].*$", re.M)
    match = heading.search(text)
    if not match:
        raise SystemExit(f"no CHANGELOG.md section for {version}")
    following = re.compile(r"^## \[", re.M).search(text, match.end())
    body = text[match.start() : following.start() if following else len(text)]
    lines = body.splitlines()[1:]  # drop the "## [x.y.z] — date" heading; the release title carries the version
    return "\n".join(lines).strip() + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    tag = sys.argv[1]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise SystemExit(f"not a release tag: {tag}")
    notes = section(CHANGELOG.read_text(encoding="utf-8"), tag.removeprefix("v"))
    sys.stdout.write(absolute_links(notes, tag))
