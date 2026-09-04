"""Image extractor — multi-strategy cascade for scraping article images.

Strategies (in priority order):
1. RSS feed summary embedded <img> tags (no extra HTTP request)
2. newspaper3k article.top_image (best-in-class for news sites)
3. trafilatura bare_extraction(include_images=True) + metadata
4. JSON-LD structured data image field
5. og:image / twitter:image meta tags
6. First large body image (with lazy-load data-src/data-original support)

Each candidate is validated via URL reachability check (HEAD request,
Content-Type must be image, file size > 2KB).

Learned from: codelucas/newspaper (newspaper3k), adbar/trafilatura,
erikriver/opengraph.
"""
import re
import os
import json
import time
from typing import Optional, List, Dict
from urllib.parse import urljoin, urlparse

from utils.http import fetch_url, DEFAULT_HEADERS


# ─── Configuration ───────────────────────────────────────────────

# Domains / patterns that indicate non-content images — skip these
SKIP_PATTERNS = [
    'google-analytics.com', 'googletagmanager.com', 'facebook.com/tr',
    'doubleclick.net', 'scorecardresearch.com', 'quantserve.com',
    'hotjar.com', 'clarity.ms', 'segment.com', 'mixpanel.com',
    'gravatar.com', 'avatar', 'favicon', 'logo', 'icon',
    'spacer', 'pixel.gif', 'blank.gif', '1x1', 'tracking', 'beacon',
    'placeholder', 'loading', 'spinner', 'default-thumb',
    'qrcode', 'qr_code', 'ad_banner', 'sponsor',
]

# Minimum image dimensions (width x height) for HTML attribute filtering
MIN_WIDTH = 200
MIN_HEIGHT = 150

# Minimum file size in bytes for downloaded images (filters tracking pixels)
MIN_FILE_SIZE = 2048

# Max number of article pages to fetch for image extraction
MAX_FETCHES = 60

# Request timeout for image validation HEAD requests
VALIDATE_TIMEOUT = 8


# ─── URL validation ──────────────────────────────────────────────

def _is_valid_image_url(url: str) -> bool:
    """Check if a URL looks like a valid content image URL."""
    if not url or len(url) < 10:
        return False

    url_lower = url.lower().strip()

    # Skip data URIs and SVGs
    if url_lower.startswith('data:'):
        return False
    if url_lower.endswith('.svg') or '.svg?' in url_lower:
        return False

    # Skip known ad/tracking/icon patterns
    for skip in SKIP_PATTERNS:
        if skip in url_lower:
            return False

    # Must have a valid scheme
    if not url_lower.startswith(('http://', 'https://')):
        return False

    return True


def validate_image_url(url: str, timeout: int = VALIDATE_TIMEOUT) -> bool:
    """Verify that an image URL is actually reachable and is an image.

    Uses HEAD request first; falls back to GET with range if HEAD fails.
    Checks Content-Type and Content-Length.
    """
    if not _is_valid_image_url(url):
        return False

    import requests

    headers = {**DEFAULT_HEADERS, 'Referer': url}

    # Try HEAD first
    try:
        resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            content_type = resp.headers.get('Content-Type', '').lower()
            content_length = resp.headers.get('Content-Length', '0')
            # Accept image/* and octet-stream (some CDNs use generic type)
            if 'image' in content_type or 'octet-stream' in content_type:
                if content_length and content_length.isdigit():
                    if int(content_length) >= MIN_FILE_SIZE:
                        return True
                # No Content-Length header — still valid if content-type is image
                if 'image' in content_type:
                    return True
        # 405 = HEAD not allowed, fall through to GET
    except Exception:
        pass

    # Fallback: GET with Range header (download only first bytes)
    try:
        range_headers = {**headers, 'Range': 'bytes=0-1023'}
        resp = requests.get(url, headers=range_headers, timeout=timeout, stream=True, allow_redirects=True)
        if resp.status_code in (200, 206):
            content_type = resp.headers.get('Content-Type', '').lower()
            if 'image' in content_type or 'octet-stream' in content_type:
                # Read a small chunk to verify there's actual content
                chunk = next(resp.iter_content(chunk_size=1024), b'')
                resp.close()
                if len(chunk) >= 100:  # At least some real data
                    return True
        resp.close()
    except Exception:
        pass

    return False


# ─── Strategy 1: RSS summary image ───────────────────────────────

def _extract_rss_image(html_fragment: str, base_url: str = '') -> Optional[str]:
    """Extract first valid image from RSS description HTML fragment."""
    if not html_fragment:
        return None

    # Find all img src in the fragment (including data-src for lazy-loaded)
    patterns = [
        r'<img[^>]+src=["\']([^"\']+)["\']',
        r'<img[^>]+data-src=["\']([^"\']+)["\']',
        r'<img[^>]+data-original=["\']([^"\']+)["\']',
    ]

    for pattern in patterns:
        urls = re.findall(pattern, html_fragment, re.IGNORECASE)
        for url in urls:
            if base_url and not url.startswith('http'):
                url = urljoin(base_url, url)
            if _is_valid_image_url(url):
                return url

    return None


# ─── Strategy 2: newspaper3k ──────────────────────────────────────

def _extract_newspaper3k(url: str, html: str = None) -> Optional[str]:
    """Use newspaper3k to extract top image from article."""
    try:
        from newspaper import Article
    except ImportError:
        return None

    try:
        article = Article(url)
        if html:
            article.html = html
            article.download_state = 2  # Mark as downloaded
        else:
            article.download()
        article.parse()
        top_img = article.top_image
        if top_img and _is_valid_image_url(top_img):
            return top_img
    except Exception:
        pass
    return None


# ─── Strategy 3: trafilatura ─────────────────────────────────────

def _extract_trafilatura(url: str, html: str = None) -> Optional[str]:
    """Use trafilatura to extract images and metadata."""
    try:
        import trafilatura
    except ImportError:
        return None

    try:
        if not html:
            html = trafilatura.fetch_url(url)
        if not html:
            return None

        # Method A: bare_extraction with include_images
        result = trafilatura.bare_extraction(html, include_images=True, url=url)
        if result:
            # trafilatura returns 'image' in metadata
            img = result.get('image') or ''
            if img and _is_valid_image_url(img):
                return img

            # Check comments section for images
            comments = result.get('comments', '')
            if comments:
                img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', comments, re.IGNORECASE)
                if img_match and _is_valid_image_url(img_match.group(1)):
                    return img_match.group(1)

        # Method B: extract_metadata for og:image
        metadata = trafilatura.extract_metadata(html)
        if metadata:
            img = metadata.image or ''
            if img and _is_valid_image_url(img):
                if not img.startswith('http'):
                    img = urljoin(url, img)
                return img
    except Exception:
        pass
    return None


# ─── Strategy 4: JSON-LD structured data ─────────────────────────

def _extract_jsonld_image(soup, base_url: str) -> Optional[str]:
    """Extract image from JSON-LD structured data (Schema.org)."""
    scripts = soup.find_all('script', attrs={'type': 'application/ld+json'})
    for script in scripts:
        try:
            text = script.string or script.get_text()
            if not text:
                continue
            data = json.loads(text)

            # Handle array of JSON-LD objects
            if isinstance(data, list):
                for item in data:
                    img = _find_image_in_jsonld(item, base_url)
                    if img:
                        return img
            elif isinstance(data, dict):
                img = _find_image_in_jsonld(data, base_url)
                if img:
                    return img
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _find_image_in_jsonld(data: dict, base_url: str) -> Optional[str]:
    """Recursively find image field in JSON-LD data."""
    if not isinstance(data, dict):
        return None

    # Direct image field
    image = data.get('image')
    if image:
        if isinstance(image, str):
            if not image.startswith('http'):
                image = urljoin(base_url, image)
            if _is_valid_image_url(image):
                return image
        elif isinstance(image, list) and image:
            for img in image:
                if isinstance(img, str):
                    if not img.startswith('http'):
                        img = urljoin(base_url, img)
                    if _is_valid_image_url(img):
                        return img
        elif isinstance(image, dict):
            url_val = image.get('url') or image.get('contentUrl')
            if url_val and isinstance(url_val, str):
                if not url_val.startswith('http'):
                    url_val = urljoin(base_url, url_val)
                if _is_valid_image_url(url_val):
                    return url_val

    # Check nested objects (mainEntity, etc.)
    for key in ['mainEntity', 'mainEntityOfPage', 'isPartOf', 'publisher']:
        nested = data.get(key)
        if isinstance(nested, dict):
            img = _find_image_in_jsonld(nested, base_url)
            if img:
                return img

    return None


# ─── Strategy 5: og:image / twitter:image meta ───────────────────

def _extract_og_image(soup) -> Optional[str]:
    """Extract og:image or twitter:image from meta tags."""
    # og:image variants
    for attr_name in ['property', 'name']:
        for attr_val in ['og:image', 'og:image:url', 'og:image:secure_url',
                         'twitter:image', 'twitter:image:src']:
            tag = soup.find('meta', attrs={attr_name: attr_val})
            if tag and tag.get('content'):
                url = tag['content'].strip()
                if _is_valid_image_url(url):
                    return url

    # Also check <link rel="image_src">
    link = soup.find('link', attrs={'rel': 'image_src'})
    if link and link.get('href'):
        url = link['href'].strip()
        if _is_valid_image_url(url):
            return url

    return None


# ─── Strategy 6: body images ─────────────────────────────────────

def _extract_body_images(soup, base_url: str, max_count: int = 10) -> List[str]:
    """Extract candidate content images from article body.

    Tries common content selectors first, then falls back to all images.
    Handles lazy-loaded images with data-src, data-original, data-lazy-src.
    """
    candidates = []

    # Try common article content selectors (priority order)
    content_selectors = [
        'article img',
        '.article-content img', '.post-content img', '.entry-content img',
        '.content img', 'main img', '.news-content img',
        '.article-body img', '.post-body img', '.article-text img',
        '#article img', '#content img', '.story-body img',
        '.rich-text img', '.markdown-body img',
    ]

    images = []
    for selector in content_selectors:
        images = soup.select(selector)
        if images:
            break

    # Fallback: all images
    if not images:
        images = soup.find_all('img')

    for img in images:
        # Try multiple src attributes (lazy-load support)
        url = (img.get('src') or img.get('data-src') or
               img.get('data-original') or img.get('data-lazy-src') or
               img.get('data-actualsrc') or '')

        if not url:
            continue

        # Fix relative URLs
        if not url.startswith('http'):
            url = urljoin(base_url, url)

        if not _is_valid_image_url(url):
            continue

        # Check width/height attributes if available
        width = img.get('width', '')
        height = img.get('height', '')
        if width and width.isdigit() and int(width) < MIN_WIDTH:
            continue
        if height and height.isdigit() and int(height) < MIN_HEIGHT:
            continue

        candidates.append(url)
        if len(candidates) >= max_count:
            break

    return candidates


# ─── Main extraction function ─────────────────────────────────────

def extract_image(url: str, rss_summary: str = '', fetched_html: str = None,
                  validate: bool = True) -> Optional[str]:
    """Extract the main image from a news article URL.

    Uses a multi-strategy cascade:
    1. RSS summary embedded image (no HTTP request)
    2. newspaper3k top_image
    3. trafilatura extraction + metadata
    4. JSON-LD structured data
    5. og:image / twitter:image meta tags
    6. First large body image

    Each candidate is validated for reachability (if validate=True).

    Args:
        url: Article URL
        rss_summary: HTML summary from RSS feed (may contain img tags)
        fetched_html: Pre-fetched HTML content (optional, avoids duplicate fetch)
        validate: If True, verify image URL is reachable before returning

    Returns:
        Validated image URL or None
    """
    # ── Strategy 1: RSS summary (cheapest — no HTTP request) ──
    if rss_summary:
        img = _extract_rss_image(rss_summary, url)
        if img:
            if not validate or validate_image_url(img):
                return img

    # ── Fetch HTML once for strategies 2-6 ──
    html = fetched_html or fetch_url(url)
    if not html:
        return None

    soup = None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
    except ImportError:
        # No BeautifulSoup — use regex fallbacks
        # Strategy 5 via regex: og:image
        og_match = re.search(
            r'<meta[^>]+(?:property|name)=["\'](?:og:image|og:image:url|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE
        )
        if og_match and _is_valid_image_url(og_match.group(1)):
            candidate = og_match.group(1)
            if not validate or validate_image_url(candidate):
                return candidate

        # Strategy 6 via regex: first img src
        for pattern in [
            r'<img[^>]+src=["\']([^"\']+)["\']',
            r'<img[^>]+data-src=["\']([^"\']+)["\']',
        ]:
            for match in re.finditer(pattern, html, re.IGNORECASE):
                img_url = match.group(1)
                if not img_url.startswith('http'):
                    img_url = urljoin(url, img_url)
                if _is_valid_image_url(img_url):
                    if not validate or validate_image_url(img_url):
                        return img_url
        return None

    # ── Strategy 2: newspaper3k ──
    img = _extract_newspaper3k(url, html)
    if img:
        if not validate or validate_image_url(img):
            return img

    # ── Strategy 3: trafilatura ──
    img = _extract_trafilatura(url, html)
    if img:
        if not validate or validate_image_url(img):
            return img

    # ── Strategy 4: JSON-LD structured data ──
    img = _extract_jsonld_image(soup, url)
    if img:
        if not validate or validate_image_url(img):
            return img

    # ── Strategy 5: og:image / twitter:image meta ──
    img = _extract_og_image(soup)
    if img:
        if not validate or validate_image_url(img):
            return img

    # ── Strategy 6: body images (try multiple candidates) ──
    candidates = _extract_body_images(soup, url)
    for candidate in candidates:
        if not validate or validate_image_url(candidate):
            return candidate

    return None


# ─── Batch processing ─────────────────────────────────────────────

def extract_images_batch(items: list, max_fetches: int = MAX_FETCHES,
                        validate: bool = True) -> list:
    """Extract images for a batch of news items.

    Modifies items in place, adding 'image_url' field.
    Skips items that already have a valid image_url.

    Args:
        items: List of news item dicts
        max_fetches: Maximum number of pages to fetch
        validate: If True, verify each image URL is reachable

    Returns:
        The modified items list
    """
    fetched = 0
    for i, item in enumerate(items):
        if fetched >= max_fetches:
            print(f"  [IMG] Reached max fetches ({max_fetches}), stopping")
            break

        # Skip if already has a valid image_url
        existing = item.get('image_url', '')
        if existing and _is_valid_image_url(existing):
            if not validate or validate_image_url(existing):
                continue

        url = item.get('url', '')
        rss_summary = item.get('summary', '')

        if not url:
            item['image_url'] = ''
            continue

        title = item.get('cn_title') or item.get('title', '')
        print(f"  [IMG] ({i+1}/{len(items)}) {title[:35]}...")

        image_url = extract_image(url, rss_summary, validate=validate)

        if image_url:
            item['image_url'] = image_url
            print(f"    ✅ {image_url[:70]}")
        else:
            item['image_url'] = ''
            print(f"    ⚠️ No valid image found")

        fetched += 1
        time.sleep(0.3)  # Rate limiting between fetches

    found = sum(1 for i in items if i.get('image_url'))
    print(f"  [IMG] Total: {found}/{len(items)} items have images")
    return items


# ─── Image download (for local use / WeChat migration) ───────────

def download_image(url: str, save_path: str = None) -> Optional[str]:
    """Download an image to local file. Returns local path or None.

    Validates: Content-Type is image, file size >= MIN_FILE_SIZE.
    """
    import requests

    if save_path is None:
        # Use extension from URL if available, else .jpg
        ext = '.jpg'
        for e in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
            if e in url.lower():
                ext = e
                break
        save_path = f"/tmp/news_img_{hash(url) % 100000}{ext}"

    headers = {**DEFAULT_HEADERS, 'Referer': url}

    try:
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get('Content-Type', '')
        if 'image' not in content_type and 'octet-stream' not in content_type:
            return None

        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        if os.path.getsize(save_path) < MIN_FILE_SIZE:
            os.remove(save_path)
            return None

        return save_path
    except Exception as e:
        print(f"  [IMG] Download failed for {url[:60]}: {e}")
        try:
            if os.path.exists(save_path):
                os.remove(save_path)
        except OSError:
            pass
        return None
