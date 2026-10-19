"""GitHub Trending collector for the 开源热榜 column."""
import re
import time
import random
from typing import List, Dict, Optional
from utils.http import fetch_url
from utils.text import clean_text

# trending 条目的路径只有 owner/repo 这一种合法形状
_REPO_PATH_RE = re.compile(r'^[\w.-]+/[\w.-]+$')


def collect_github_trending(config: Dict) -> List[Dict]:
    """Collect GitHub trending AI/ML repositories.

    Fetches from GitHub Trending page, filtering by AI-related languages/keywords.
    Learned from CloudFlare-AI-Insight-Daily's github-trending.js pattern.
    """
    max_items = config.get('max_items', 8)
    since = config.get('since', 'daily')
    languages = config.get('languages', ['python', 'jupyter-notebook'])
    keyword_filter = config.get('keyword_filter',
        'AI,ML,LLM,GPT,transformer,diffusion,neural,agent,RAG,vision-language,'
        'machine learning,deep learning,large language model,fine-tune,embedding,'
        'CLIP,stable diffusion,langchain,autonomous,reasoning'
    )

    all_items = []

    for lang in languages:
        url = f"https://github.com/trending/{lang}?since={since}"
        print(f"  [GITHUB] Fetching trending {lang}: {url}")
        html = fetch_url(url)
        if not html:
            continue
        time.sleep(random.uniform(0.5, 1.5))

        repos = _parse_trending_page(html, lang)
        all_items.extend(repos)

    # Filter by AI keywords
    filtered = []
    for repo in all_items:
        text_to_check = f"{repo['title']} {repo['summary']}".lower()
        if _matches_keywords(text_to_check, keyword_filter):
            filtered.append(repo)

    # Deduplicate by repo URL
    seen_urls = set()
    unique = []
    for item in filtered:
        if item['url'] not in seen_urls:
            seen_urls.add(item['url'])
            unique.append(item)

    result = unique[:max_items]
    print(f"  [GITHUB] Total trending repos: {len(all_items)}, AI-filtered: {len(result)}")
    return result


def _parse_trending_page(html: str, lang: str) -> List[Dict]:
    """Parse GitHub trending HTML page to extract repo info."""
    items = []
    # Each repo is in an <article> tag
    repo_blocks = re.findall(
        r'<article[^>]*class="Box-row"[^>]*>(.*?)</article>',
        html, re.DOTALL
    )
    if not repo_blocks:
        # Try alternative pattern
        repo_blocks = re.findall(
            r'<div class="Box-row[^"]*"[^>]*>(.*?)</div>\s*(?=<div class="Box-row|</div>\s*</div>)',
            html, re.DOTALL
        )

    for block in repo_blocks:
        # Extract repo URL and name
        url_match = re.search(r'href="(/[^"]+)"', block)
        if not url_match:
            continue
        repo_path = url_match.group(1).strip()
        # Clean path (remove leading /)
        if repo_path.startswith('/'):
            repo_path = repo_path[1:]
        # GitHub 偶发把趋势页锚点换成登录跳转（/login?return_to=%2Fowner%2Frepo），
        # 这类条目既不是仓库，标题也是一串 URL 参数，直接丢掉
        if not _REPO_PATH_RE.match(repo_path):
            continue
        full_url = f"https://github.com/{repo_path}"
        repo_name = repo_path

        # Extract description
        desc_match = re.search(r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
        if not desc_match:
            desc_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        description = clean_text(desc_match.group(1)) if desc_match else ''

        # Extract language
        lang_match = re.search(
            r'<span[^>]*itemprop="programmingLanguage"[^>]*>(.*?)</span>',
            block, re.DOTALL
        )
        repo_lang = clean_text(lang_match.group(1)) if lang_match else lang

        # Extract stars today
        stars_today_match = re.search(
            r'(\d[\d,]*)\s*stars?\s*(?:today|this week|this month)',
            block, re.IGNORECASE
        )
        stars_today = stars_today_match.group(1).replace(',', '') if stars_today_match else '0'

        # Extract total stars
        total_stars_match = re.findall(r'/stargazers"[^>]*>([\d,]+)</a>', block)
        total_stars = total_stars_match[-1].replace(',', '') if total_stars_match else '0'

        if not repo_name or len(repo_name) < 3:
            continue

        items.append({
            'title': repo_name,
            'summary': description[:300] if description else f'GitHub trending {repo_lang} project with {stars_today} stars today',
            'url': full_url,
            'source': 'GitHub Trending',
            'source_domain': 'github.com',
            'category_hint': '开源热榜',
            'lang': 'en',
            'weight': 1.0,
            'pub_date': '',
            'is_paper': False,
            'is_github_trending': True,
            'stars_today': int(stars_today) if stars_today.isdigit() else 0,
            'total_stars': int(total_stars) if total_stars.isdigit() else 0,
            'repo_language': repo_lang,
        })

    return items


def _matches_keywords(text: str, keywords: str) -> bool:
    """Check if text matches any of the comma-separated keywords."""
    if not keywords:
        return True
    for kw in keywords.split(','):
        kw = kw.strip().lower()
        if kw and kw in text:
            return True
    return False
