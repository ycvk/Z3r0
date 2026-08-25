from __future__ import annotations

import json
from html.parser import HTMLParser

import httpx
from agents import function_tool


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._capture_title = False
        self._current_href = ""
        self._current_title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        href = (attributes.get("href") or "").strip()
        css = attributes.get("class") or ""
        if href.startswith("http") and ("result__a" in css or "result-link" in css or "result__url" not in css):
            self._capture_title = True
            self._current_href = href
            self._current_title = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._capture_title:
            return
        title = " ".join("".join(self._current_title).split())
        if self._current_href and title:
            self.results.append({
                "title": title,
                "url": self._current_href,
                "snippet": "",
            })
        self._capture_title = False
        self._current_href = ""
        self._current_title = []

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._current_title.append(data)


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
    except Exception as exc:
        return json.dumps({"status": "failed", "error": str(exc) or "web search failed"})

    parser = _DuckDuckGoParser()
    try:
        parser.feed(response.text)
    except Exception as exc:
        return json.dumps({"status": "failed", "error": str(exc) or "web search parse failed"})
    return json.dumps({"results": parser.results[:size]})
