from __future__ import annotations

import json
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from agents import function_tool


def _normalize_result_url(href: str) -> str:
    href = href.strip()
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin("https://duckduckgo.com", href)
    parsed = urlparse(href)
    encoded = (parse_qs(parsed.query).get("uddg") or [""])[0]
    if encoded:
        return unquote(encoded)
    return href


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._capture = ""
        self._current_href = ""
        self._current_title: list[str] = []
        self._current_snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        href = (attributes.get("href") or "").strip()
        css = attributes.get("class") or ""
        if "result__a" in css.split():
            self._flush()
            self._capture = "title"
            self._current_href = _normalize_result_url(href)
            self._current_title = []
            self._current_snippet = []
            return
        if "result__snippet" in css.split() and self._current_href:
            self._capture = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture:
            if self._capture == "title":
                self._capture = ""
            elif self._capture == "snippet":
                self._flush()

    def handle_data(self, data: str) -> None:
        if self._capture == "title":
            self._current_title.append(data)
        elif self._capture == "snippet":
            self._current_snippet.append(data)

    def close(self) -> None:
        self._flush()
        super().close()

    def _flush(self) -> None:
        title = " ".join("".join(self._current_title).split())
        snippet = " ".join("".join(self._current_snippet).split())
        if self._current_href and title:
            self.results.append({
                "title": title,
                "url": self._current_href,
                "snippet": snippet,
            })
        self._capture = ""
        self._current_href = ""
        self._current_title = []
        self._current_snippet = []


@function_tool
async def web_search(query: str, limit: int = 8) -> str:
    """Search the public web.

    Returns JSON `{results:[{title,url,snippet}]}` or `{status:"failed",error}`.
    """
    cleaned = " ".join((query or "").split())
    if not cleaned or len(cleaned) > 200:
        return json.dumps({"status": "failed", "error": "query must be 1-200 characters"})
    size = min(max(int(limit or 8), 1), 8)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": cleaned},
                headers={"User-Agent": "Mozilla/5.0 Z3r0-Flow/0.2"},
            )
            response.raise_for_status()
    except Exception as extra:
        return json.dumps({"status": "failed", "error": str(extra) or "web search failed"})

    parser = _DuckDuckGoParser()
    try:
        parser.feed(response.text)
        parser.close()
    except Exception as extra:
        return json.dumps({"status": "failed", "error": str(extra) or "web search parse failed"})
    results = parser.results[:size]
    if not results:
        return json.dumps({"status": "failed", "error": "web search returned no results"})
    return json.dumps({"results": results})
