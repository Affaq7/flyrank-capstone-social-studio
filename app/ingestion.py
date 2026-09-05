import requests
import trafilatura
from bs4 import BeautifulSoup

# Some sites 403 the default python-requests UA as basic bot-blocking.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def _strip_all_text(html: str) -> str:
    """Last-resort fallback: every visible string on the page, nav/ads/footer
    included. Used only when trafilatura's main-content extraction fails."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


def fetch_url_content(url: str) -> str:
    """Fetch a URL once and extract its main article content — not the raw
    page text, which pulls in nav/ads/footer/comments alongside the actual
    content. Called only at ingestion time — nothing downstream re-fetches
    or re-parses the source."""
    response = requests.get(url, timeout=10, headers=_HEADERS)
    response.raise_for_status()

    extracted = trafilatura.extract(response.text)
    if extracted:
        return " ".join(extracted.split())

    return _strip_all_text(response.text)
