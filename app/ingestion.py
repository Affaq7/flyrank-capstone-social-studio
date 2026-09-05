import requests
from bs4 import BeautifulSoup


def fetch_url_content(url: str) -> str:
    """Fetch a URL once and extract its visible text. Called only at
    ingestion time — nothing downstream re-fetches or re-parses the source."""
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    return " ".join(soup.get_text(separator=" ").split())
