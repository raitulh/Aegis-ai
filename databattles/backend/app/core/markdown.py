"""Safe Markdown rendering.

Markdown is rendered server-side with raw HTML disabled, then sanitized with an
allow-list (nh3). Clients only ever receive sanitized HTML.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

import nh3
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable(["table", "strikethrough"])

_ALLOWED_TAGS = {
    "p", "br", "hr", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "code", "pre", "blockquote",
    "ul", "ol", "li", "a", "table", "thead", "tbody", "tr", "th", "td", "img", "span",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "code": {"class"},
    "th": {"align"},
    "td": {"align"},
    "span": {"class"},
}
MENTION_RE = re.compile(r"(?<![\w@/])@([a-z0-9][a-z0-9_-]{1,28}[a-z0-9])\b")


def _demote_h1(html: str) -> str:
    # Page titles own <h1>; user content starts at <h2> to keep heading hierarchy valid.
    return re.sub(r"<(/?)h1>", r"<\1h2>", html)


def render_markdown(text: str | None, *, resolve_mentions: Callable[[Iterable[str]], set[str]] | None = None) -> str:
    if not text:
        return ""
    source = text
    if resolve_mentions is not None:
        handles = set(MENTION_RE.findall(source))
        valid = resolve_mentions(handles) if handles else set()
        if valid:
            source = MENTION_RE.sub(lambda m: f"[@{m.group(1)}](/u/{m.group(1)})" if m.group(1) in valid else m.group(0), source)
    html = _demote_h1(_md.render(source))
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="nofollow noopener noreferrer ugc",
        strip_comments=True,
    )


def extract_mentions(text: str | None) -> set[str]:
    return set(MENTION_RE.findall(text or ""))


def plain_text(text: str | None, limit: int = 4000) -> str:
    """Strip markdown/HTML to plain text for search indexing and previews."""
    if not text:
        return ""
    html = _md.render(text)
    cleaned = nh3.clean(html, tags=set())
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]
