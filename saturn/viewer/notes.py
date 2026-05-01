"""Per-finding annotation sidecar.

If `~/saturn-findings/<id>.notes.md` exists, render it as sanitised HTML and
surface alongside the LLM reading. The flow assumes researchers manage the
markdown via filesystem (vim, an editor, scp) — there is no in-app form for
writing notes. That keeps the contract simple: edit the file, refresh the
page, your annotation appears.

Sanitisation runs every output through bleach with an allowlist of
narrative-relevant tags. Unsupported HTML is stripped, not escaped, so a
researcher's accidental angle bracket doesn't dump as `&lt;` literal.
"""

from __future__ import annotations

from pathlib import Path

import bleach
import markdown


# Tags + attributes a research note legitimately needs. No <img>, <iframe>,
# <script>, <style>, no inline styles, no on* handlers. Anchors are href-only.
_ALLOWED_TAGS = [
    "p", "br", "hr",
    "strong", "em", "b", "i", "u", "s", "del", "ins", "mark",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "a", "blockquote", "code", "pre", "kbd", "samp",
    "table", "thead", "tbody", "tr", "th", "td",
    "sup", "sub", "abbr", "dfn", "cite",
]
_ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
    "abbr": ["title"],
    "th": ["scope"],
    "*": ["id"],  # heading anchors only
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def render_notes(path: Path) -> str | None:
    """Read a sidecar `.notes.md` and return sanitised HTML.

    Returns None when the file is absent, empty, or unreadable. Never raises
    — a corrupted notes file shouldn't 500 the entire view.
    """
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None

    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html",
    )
    html = md.convert(raw)

    # bleach.clean strips disallowed tags rather than escaping them; for
    # research notes this matches "do what I mean" better.
    cleaned = bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
    # Auto-link bare URLs so a pasted DOI or arXiv link becomes clickable.
    cleaned = bleach.linkify(
        cleaned,
        callbacks=[lambda attrs, new=False: {**attrs, (None, "rel"): "nofollow noopener"}],
    )
    return cleaned or None


def notes_path_for(findings_dir: Path, finding_id: str) -> Path:
    """The sibling `.notes.md` path beside `<finding_id>.json`."""
    return findings_dir / f"{finding_id}.notes.md"
