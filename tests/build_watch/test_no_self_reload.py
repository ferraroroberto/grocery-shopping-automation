"""The shell never reloads itself on a build change.

A newer build is offered by the vendored build-watch prompt, which reloads only
on a tap; a reload the page triggers on its own can drop unsent input.
"""

import re

from app.tray.tray import PROJECT_ROOT

STATIC_DIR = PROJECT_ROOT / "app" / "webapp" / "static"
_RELOAD = re.compile(r"location\s*\.\s*reload\s*\(")


def test_no_page_script_reloads_the_page_by_itself() -> None:
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for pattern in ("*.js", "*.html")
        for path in STATIC_DIR.rglob(pattern)
        if "_vendored" not in path.relative_to(STATIC_DIR).parts
        and _RELOAD.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
