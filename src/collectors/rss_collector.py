"""RSS feed collector."""
import feedparser
from typing import List, Dict
from utils.text import clean_text, keyword_match, is_recent, parse_date
from utils.http import fetch_url


def collect_rss(feed_config: Dict, lookback_hours: int = 24) -> List[Dict]:
    """Collect items from an RSS feed."""
    name = feed_config.get('name', 'Unknown')
    url = feed_config['url']
    category = feed_config.get('category', '行业动态')
    lang = feed_config.get('lang', 'zh')
    weight = feed_config.get('weight', 1.0)
    keyword_filter = feed_config.get('keyword_filter', '')
    keyword_exclude = feed_config.get('keyword_exclude', '')

    print(f"  [RSS] Fetching: {name} ({url})")
    try:
        feed = feedparser.parse(url, request_headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        if not feed.entries:
            print(f"    [!] No entries from {name}")
            return []

        items = []
        for entry in feed.entries[:30]:
            title = clean_text(entry.get('title', ''))
            summary = clean_text(entry.get('summary', entry.get('description', '')))
            link = entry.get('link', '')
            pub_date = entry.get('published', entry.get('updated', ''))

            if not title or not link:
                continue

            # Apply keyword filter
            combined_text = f"{title} {summary}"
            if keyword_filter and not keyword_match(combined_text, keyword_filter):
                continue
            # 排除词优先：消费电子/汽车等噪音常因摘要里带"智能"字样而漏过正向过滤
            if keyword_exclude and keyword_match(combined_text, keyword_exclude):
                continue

            # Check recency
            if pub_date and not is_recent(pub_date, lookback_hours * 2):  # 2x buffer
                continue

            items.append({
                'title': title,
                'summary': summary[:300] if summary else '',
                'url': link,
                'source': name,
                'source_domain': '',
                'category_hint': category,
                'lang': lang,
                'weight': weight,
                'pub_date': pub_date,
                'is_paper': False,
            })

        print(f"    [OK] {name}: {len(items)} items")
        return items
    except Exception as e:
        print(f"    [!] Error fetching {name}: {e}")
        return []
