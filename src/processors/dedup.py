"""Deduplication processor."""
import re
from typing import List, Dict
from utils.text import similarity, extract_domain

# 采集器偶尔抓到登录页／跳转页而不是正文（例如 github.com/login?return_to=...），
# 这类条目的标题是 URL 片段，进了日报就是脏数据，去重阶段先拦掉
JUNK_PATH_RE = re.compile(r'/(login|log-in|signin|sign-in|signup|sign-up|register)(/?$|\?|/)')
JUNK_TITLE_PREFIXES = ('http://', 'https://', 'login?', 'www.', '%2f', '%2F')


def is_junk_item(item: Dict) -> bool:
    """判断条目是否其实是登录页／跳转页而非新闻。"""
    url = (item.get('url') or '').lower()
    title = ((item.get('cn_title') or item.get('title') or '')).strip()
    if JUNK_PATH_RE.search(url) or 'return_to=' in url:
        return True
    if title.startswith(JUNK_TITLE_PREFIXES) or 'return_to=' in title:
        return True
    return False


def deduplicate(items: List[Dict], threshold: float = 0.65) -> List[Dict]:
    """Remove duplicate items based on title similarity and URL."""
    if not items:
        return []

    result = []
    seen_urls = set()
    seen_titles = []
    junk = 0

    for item in items:
        if is_junk_item(item):
            junk += 1
            continue

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
    print(f"  [Dedup] {len(items)} → {len(result)} (removed {removed - junk} duplicates, {junk} junk pages)")
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
