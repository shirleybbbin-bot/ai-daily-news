"""HTTP client utilities for fetching web content."""
import requests
import time
import random
from typing import Optional

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

def fetch_url(url: str, headers: Optional[dict] = None, timeout: int = 15, retries: int = 3) -> Optional[str]:
    """Fetch URL content with retries and random backoff."""
    h = {**DEFAULT_HEADERS, **(headers or {})}
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=h, timeout=timeout)
            resp.encoding = resp.apparent_encoding or resp.encoding
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(random.uniform(1, 3))
            else:
                print(f"  [!] Failed to fetch {url}: {e}")
                return None
    return None

def fetch_json(url: str, headers: Optional[dict] = None, timeout: int = 15) -> Optional[dict]:
    """Fetch JSON from URL."""
    h = {**DEFAULT_HEADERS, **(headers or {})}
    try:
        resp = requests.get(url, headers=h, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  [!] Failed to fetch JSON {url}: {e}")
        return None
