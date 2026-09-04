"""Web page scraper for sites without RSS."""
from bs4 import BeautifulSoup
from typing import List, Dict
from utils.text import clean_text
from utils.http import fetch_url


def collect_web(source_config: Dict) -> List[Dict]:
    """Collect items from a web page."""
    name = source_config.get('name', 'Unknown')
    url = source_config['url']
    category = source_config.get('category', '行业动态')
    lang = source_config.get('lang', 'zh')
    weight = source_config.get('weight', 1.0)

    print(f"  [WEB] Scraping: {name} ({url})")
    html = fetch_url(url)
    if not html:
        print(f"    [!] Failed to fetch {name}")
        return []

    soup = BeautifulSoup(html, 'lxml')
    items = []

    # Generic extraction: try to find article-like elements
    item_selector = source_config.get('selector', 'article, .news-item, .list-item, .post-item')
    title_selector = source_config.get('title_selector', 'h1, h2, h3, .title, .news-title')
    link_selector = source_config.get('link_selector', 'a')
    summary_selector = source_config.get('summary_selector', 'p, .summary, .desc, .excerpt')

    elements = soup.select(item_selector)

    # If no specific items found, try all links with titles
    if not elements:
        elements = soup.select(title_selector)

    for elem in elements[:30]:
        title_elem = elem.select_one(title_selector) if title_selector else elem
        link_elem = elem.select_one(link_selector) if link_selector else elem.find('a')
        summary_elem = elem.select_one(summary_selector) if summary_selector else None

        title = clean_text(title_elem.get_text() if title_elem else '')
        link = link_elem.get('href', '') if link_elem else ''
        summary = clean_text(summary_elem.get_text() if summary_elem else '')

        if not title:
            # Try to get title from the element itself
            title = clean_text(elem.get_text())

        if not title or len(title) < 5:
            continue

        # Fix relative URLs
        if link and not link.startswith('http'):
            if link.startswith('/'):
                from urllib.parse import urljoin
                link = urljoin(url, link)

        if not link:
            link = url

        items.append({
            'title': title,
            'summary': summary[:300] if summary else '',
            'url': link,
            'source': name,
            'source_domain': '',
            'category_hint': category,
            'lang': lang,
            'weight': weight,
            'pub_date': '',
            'is_paper': False,
        })

    # Deduplicate within source
    seen = set()
    unique = []
    for item in items:
        key = item['title'].lower()
        if key not in seen and item['url'] != url:
            seen.add(key)
            unique.append(item)

    print(f"    [OK] {name}: {len(unique)} items")
    return unique
