"""Deduplication processor."""
from typing import List, Dict
from utils.text import similarity, extract_domain


def deduplicate(items: List[Dict], threshold: float = 0.65) -> List[Dict]:
    """Remove duplicate items based on title similarity and URL."""
    if not items:
        return []

    result = []
    seen_urls = set()
    seen_titles = []

    for item in items:
        url = item.get('url', '')
        title = item.get('cn_title') or item.get('title', '')

        # URL dedup
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Title similarity dedup
        is_dup = False
        for seen_title in seen_titles:
            sim = similarity(title, seen_title)
            if sim > threshold:
                is_dup = True
                break

        if not is_dup:
            result.append(item)
            seen_titles.append(title)

    removed = len(items) - len(result)
    print(f"  [Dedup] {len(items)} → {len(result)} (removed {removed} duplicates)")
    return result


def cross_day_dedup(items: List[Dict], prev_titles: List[str], threshold: float = 0.7) -> List[Dict]:
    """Remove items that appeared in previous days."""
    if not prev_titles:
        return items

    result = []
    for item in items:
        title = item.get('cn_title') or item.get('title', '')
        is_dup = False
        for prev_title in prev_titles:
            if similarity(title, prev_title) > threshold:
                is_dup = True
                break
        if not is_dup:
            result.append(item)

    removed = len(items) - len(result)
    if removed > 0:
        print(f"  [CrossDayDedup] Removed {removed} items from previous days")
    return result
