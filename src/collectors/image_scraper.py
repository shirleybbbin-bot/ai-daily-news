"""Image scraper — fetches og:image / article images from source URLs."""
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
from typing import List, Dict

import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

OG_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', re.I),
]

IMG_PATTERN = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)

BAD_KEYWORDS = [
    'icon', 'logo', 'avatar', 'data:', 'pixel', 'tracker', 'spacer', 'blank',
    'banner', 'ad-', 'advert', 'score', 'rating', 'btn', 'button',
    'arrow', 'search', 'menu', 'close', 'loading', 'placeholder',
    'headvip', 'arxiv-logo', 'qbitai-logo', 'qrcode', 'default',
    'funders', 'simons-fo', 'profile_images', 'tc-backlig',
]


def _is_good_image(url: str) -> bool:
    url_lower = url.lower()
    if not re.search(r'\.(jpg|jpeg|png|webp|gif)', url_lower):
        return False
    for bad in BAD_KEYWORDS:
        if bad in url_lower:
            return False
    return True


def _make_absolute(url: str, base: str) -> str:
    if url.startswith('//'):
        return 'https:' + url
    elif url.startswith('/'):
        return urljoin(base, url)
    return url


def fetch_image(item: Dict) -> Dict:
    """Fetch og:image from a single article. Returns the item with image_url set."""
    url = item.get('url', '')
    if not url or not url.startswith('http'):
        return item
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return item
        html = resp.text[:80000]

        # Try og:image / twitter:image first
        for pattern in OG_PATTERNS:
            match = pattern.search(html)
            if match:
                img_url = _make_absolute(match.group(1), url)
                if _is_good_image(img_url):
                    item['image_url'] = img_url
                    return item

        # Fallback: first reasonable <img> in body
        for match in IMG_PATTERN.finditer(html):
            img_url = _make_absolute(match.group(1), url)
            if _is_good_image(img_url):
                item['image_url'] = img_url
                return item
    except Exception:
        pass
    return item


def scrape_images(articles: List[Dict], max_workers: int = 8) -> int:
    """Scrape images for all articles in parallel. Returns count of images found."""
    found = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_image, item): i for i, item in enumerate(articles)}
        for future in as_completed(futures):
            idx = futures[future]
            result = future.result()
            articles[idx]['image_url'] = result.get('image_url', '')
            if result.get('image_url'):
                found += 1
    return found


if __name__ == '__main__':
    import sys
    data_file = sys.argv[1] if len(sys.argv) > 1 else 'today_data.json'
    with open(data_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    found = scrape_images(data)
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"配图抓取完成: {found}/{len(data)} ({found/len(data)*100:.0f}%)")
