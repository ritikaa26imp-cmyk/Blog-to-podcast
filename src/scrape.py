"""Ingest blog content: paste, BeautifulSoup (default), Firecrawl (optional), mock last resort."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
DEMO_ARTICLE_PATH = SAMPLES_DIR / "demo_article.txt"


@dataclass
class ScrapeResult:
    title: str
    text: str
    source: str  # beautifulsoup | firecrawl | mock | pasted
    url: Optional[str] = None


def _clean_text(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _load_demo_article() -> tuple[str, str]:
    if DEMO_ARTICLE_PATH.exists():
        raw = DEMO_ARTICLE_PATH.read_text(encoding="utf-8").strip()
        lines = raw.splitlines()
        title = lines[0].strip() if lines else "Sample article"
        body = "\n".join(lines[2:] if len(lines) > 2 else lines[1:]).strip()
        return title, body
    return (
        "The Future of Voice Interfaces",
        "Voice interfaces are moving from novelty to everyday infrastructure. "
        "Treat spoken summaries as first-class outputs: a clear hook, a few beats of substance, "
        "and one actionable close.",
    )


def from_pasted_text(text: str, title: str = "Pasted article") -> ScrapeResult:
    cleaned = _clean_text(text)
    if not cleaned:
        raise ValueError("Paste some article text first.")
    return ScrapeResult(
        title=(title.strip() or "Pasted article"),
        text=cleaned[:20000],
        source="pasted",
    )


def scrape_with_beautifulsoup(url: str, timeout: int = 20) -> ScrapeResult:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; BlogToPodcast/1.0) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    for tag in soup(["script", "style", "noscript", "nav", "footer", "aside", "form"]):
        tag.decompose()

    title = (
        soup.title.string.strip()
        if soup.title and soup.title.string
        else "Untitled article"
    )
    article = soup.find("article") or soup.find("main") or soup.body
    if article is None:
        raise RuntimeError("Could not find article body")

    paragraphs = [
        p.get_text(" ", strip=True)
        for p in article.find_all(["p", "h1", "h2", "h3", "li"])
    ]
    text = _clean_text("\n\n".join(p for p in paragraphs if p and len(p) > 20))
    if len(text) < 80:
        text = _clean_text(article.get_text("\n", strip=True))
    if len(text) < 80:
        raise RuntimeError("Extracted text too short")
    return ScrapeResult(title=title, text=text[:20000], source="beautifulsoup", url=url)


def scrape_with_firecrawl(url: str, api_key: str) -> ScrapeResult:
    from firecrawl import FirecrawlApp

    app = FirecrawlApp(api_key=api_key)
    if hasattr(app, "scrape_url"):
        data = app.scrape_url(url, params={"formats": ["markdown", "html"]})
    else:
        data = app.scrape(url, formats=["markdown", "html"])

    if isinstance(data, dict):
        markdown = data.get("markdown") or data.get("content") or ""
        meta = data.get("metadata") or {}
        title = meta.get("title") or meta.get("ogTitle") or "Untitled article"
        if not markdown and data.get("html"):
            soup = BeautifulSoup(data["html"], "lxml")
            markdown = soup.get_text("\n", strip=True)
    else:
        markdown = getattr(data, "markdown", None) or getattr(data, "content", "") or ""
        meta = getattr(data, "metadata", None) or {}
        if isinstance(meta, dict):
            title = meta.get("title") or "Untitled article"
        else:
            title = getattr(meta, "title", None) or "Untitled article"

    text = _clean_text(str(markdown))
    if not text:
        raise RuntimeError("Firecrawl returned empty content")
    return ScrapeResult(title=str(title), text=text[:20000], source="firecrawl", url=url)


def mock_scrape(url: Optional[str] = None, reason: str = "") -> ScrapeResult:
    title, body = _load_demo_article()
    label = "[Demo sample article"
    if reason:
        label += f" — {reason}"
    label += "]"
    return ScrapeResult(
        title=title,
        text=f"{label}\n\n{body}",
        source="mock",
        url=url,
    )


def fetch_blog(
    url: str,
    firecrawl_key: Optional[str] = None,
    prefer_mock: bool = False,
) -> tuple[ScrapeResult, list[str]]:
    """Return article + warnings. Firecrawl only when key set; else BeautifulSoup; mock last."""
    warnings: list[str] = []
    if prefer_mock:
        return mock_scrape(url, reason="forced demo mode"), [
            "Using mock sample article (demo mode)."
        ]

    url = url.strip()
    if not url:
        raise ValueError("Enter a blog URL.")

    if firecrawl_key:
        try:
            return scrape_with_firecrawl(url, firecrawl_key), warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Firecrawl failed ({exc}); trying BeautifulSoup.")

    try:
        return scrape_with_beautifulsoup(url), warnings
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"BeautifulSoup scrape failed ({exc}); using mock sample article.")
        return mock_scrape(url, reason=str(exc)), warnings
