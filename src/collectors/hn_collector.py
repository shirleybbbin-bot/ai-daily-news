"""Hacker News collector via Firebase API."""
from typing import List, Dict
from utils.http import fetch_json
from utils.text import keyword_match


def collect_hackernews(config: Dict) -> List[Dict]:
    """Collect top stories from Hacker News, filtered by AI keywords."""
    top_n = config.get('top_n', 20)
    keyword_filter = config.get('keyword_filter', '')
    min_score = config.get('min_score', 50)
    category = config.get('category', '大V观点')

    print("  [HN] Fetching Hacker News top stories...")

    # Get top story IDs
    ids_data = fetch_json("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not ids_data:
        print("    [!] Failed to get HN top stories")
        return []

    top_ids = ids_data[:top_n * 3]  # Fetch more to filter
    items = []

    for sid in top_ids:
        if len(items) >= top_n:
            break
        story = fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
        if not story:
            continue

        title = story.get('title', '')
        score = story.get('score', 0)
        url = story.get('url', f"https://news.ycombinator.com/item?id={sid}")

        if score < min_score:
            continue

        if keyword_filter and not keyword_match(title, keyword_filter):
            continue

        items.append({
            'title': title,
            'summary': f"Hacker News 热帖，得分 {score}，评论 {story.get('descendants', 0)}",
            'url': url,
            'source': 'Hacker News',
            'source_domain': 'news.ycombinator.com',
            'category_hint': category,
            'lang': 'en',
            'weight': 1.0,
            'pub_date': '',
            'is_paper': False,
            'hn_score': score,
        })

    print(f"    [OK] Hacker News: {len(items)} items")
    return items
