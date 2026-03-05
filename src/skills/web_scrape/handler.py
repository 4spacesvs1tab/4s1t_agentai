#!/usr/bin/env python3
"""
web_scrape skill handler.

Fetches a URL using requests and extracts plain text via html.parser (stdlib).

Input:  {"parameters": {"url": "...", "max_chars": 8000}}
Output: {"success": true, "result": {"title": "...", "text": "...", "url": "...",
                                      "char_count": N, "truncated": false}}
"""
import json
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse


_DEFAULT_MAX_CHARS = 8_000
_REQUEST_TIMEOUT = 20  # seconds
_USER_AGENT = (
    "Mozilla/5.0 (compatible; 4s1t-research-agent/1.0; +https://github.com/4spacesvs1tab)"
)

# Tags whose content we discard entirely (scripts, styles, nav, etc.)
_SKIP_TAGS = frozenset([
    "script", "style", "noscript", "head", "header", "footer",
    "nav", "aside", "form", "button", "select", "option",
    "svg", "canvas", "iframe", "object", "embed",
])


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text extractor using stdlib html.parser."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        self._in_title: bool = False
        self._skip_depth: int = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title += data
            return
        stripped = data.strip()
        if stripped:
            self._parts.append(stripped + " ")

    def get_text(self) -> str:
        # Collapse excessive whitespace/newlines
        import re
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        return text.strip()


def execute(params: dict) -> dict:
    import requests

    url = params.get("url", "").strip()
    if not url:
        raise ValueError("'url' parameter is required.")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme must be http or https, got: '{parsed.scheme}'")

    max_chars = int(params.get("max_chars", _DEFAULT_MAX_CHARS))
    max_chars = max(100, min(50_000, max_chars))

    resp = requests.get(
        url,
        timeout=_REQUEST_TIMEOUT,
        headers={"User-Agent": _USER_AGENT},
        allow_redirects=True,
    )
    resp.raise_for_status()

    # Detect encoding from headers or content
    encoding = resp.encoding or "utf-8"
    html_content = resp.content.decode(encoding, errors="replace")

    parser = _TextExtractor()
    parser.feed(html_content)

    title = parser.title.strip()
    full_text = parser.get_text()
    char_count = len(full_text)
    truncated = char_count > max_chars
    text = full_text[:max_chars] if truncated else full_text

    return {
        "title": title,
        "text": text,
        "url": resp.url,
        "char_count": char_count,
        "truncated": truncated,
    }


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: handler.py input.json output.json", file=sys.stderr)
        sys.exit(1)
    input_path, output_path = sys.argv[1], sys.argv[2]
    try:
        data = json.loads(open(input_path).read())
        params = data.get("parameters", {})
        result = execute(params)
        output = {"success": True, "result": result, "error": None, "logs": []}
    except Exception as exc:
        output = {"success": False, "result": None, "error": str(exc), "logs": []}
    with open(output_path, "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()
