"""News scoring and ranking system.
Upgraded with weighted scoring learned from ai-scan + ai-daily-news:
  source_signal 40% + freshness 30% (exponential decay, 12h half-life) +
  keyword_relevance 20% + source_weight 10%
Also adds signal_grade field: 重磅 / 值得关注 / 常规
"""
import math
from typing import Dict, List
from datetime import datetime, timezone


# === Signal grade thresholds ===
GRADE_HEAVY = 55.0       # score >= 55 → 重磅
GRADE_NOTICEABLE = 25.0  # score >= 25 → 值得关注
# score < 25 → 常规


def _parse_datetime(pub_date: str):
    """Try multiple formats to parse a date string."""
    if not pub_date:
        return None
    for fmt in ['%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ',
                '%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
        try:
            dt = datetime.strptime(pub_date.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _freshness_score(pub_date: str, half_life_hours: float = 12.0) -> float:
    """Exponential decay: newer items get higher scores.

    Half-life of 12h means an item 12h old gets 50% of max freshness score.
    Returns 0–100 (normalized for the 30% weight).
    """
    dt = _parse_datetime(pub_date)
    if not dt:
        return 50.0  # unknown date → neutral

    now = datetime.now(timezone.utc)
    hours_ago = (now - dt).total_seconds() / 3600

    if hours_ago < 0:
        hours_ago = 0  # future-dated items cap at max

    # Exponential decay: score = 100 * 0.5^(hours / half_life)
    decay = 0.5 ** (hours_ago / half_life_hours)
    return 100.0 * decay


def _keyword_score(text: str) -> float:
    """Score based on AI-relevant keyword presence. Returns 0–100."""
    text_lower = text.lower()

    tier1_keywords = [
        'openai', 'gpt-5', 'gpt-4', 'gpt', 'claude', 'anthropic', 'gemini',
        'deepseek', '大模型', '开源', '融资', '收购', 'breakthrough',
        'transformer', 'diffusion', '多模态', 'agent', '推理',
    ]
    tier2_keywords = [
        'lora', 'rlhf', 'rag', 'vision-language', 'scaling', 'nvidia',
        '英伟达', '百度', '阿里', '腾讯', '字节', '华为', 'meta', 'google',
        'microsoft', 'apple', 'mistral', 'moonshot', '月之暗面', '智谱',
        'glm', '通义', 'qwen', 'perplexity', 'xai', 'grok',
    ]

    score = 0.0
    for kw in tier1_keywords:
        if kw in text_lower:
            score += 8.0
    for kw in tier2_keywords:
        if kw in text_lower:
            score += 4.0

    return min(score, 100.0)


def _source_signal_score(item: Dict) -> float:
    """Score based on engagement signals (HN score, GitHub stars). Returns 0–100."""
    score = 0.0

    hn_score = item.get('hn_score', 0)
    if hn_score > 0:
        # log2 scaling like ai-daily-news: log2(engagement) capped
        score += min(math.log2(max(hn_score, 1)) * 12, 60)

    stars_today = item.get('stars_today', 0)
    if stars_today > 0:
        score += min(math.log2(max(stars_today, 1)) * 10, 40)

    return min(score, 100.0)


def calculate_item_score(item: Dict) -> float:
    """Calculate weighted composite score.

    Weights (learned from ai-scan):
      source_signal 40% + freshness 30% + keyword_relevance 20% + source_weight 10%
    Max possible: 100
    """
    # Source signal (HN score, GitHub stars) — 40%
    signal = _source_signal_score(item)

    # Freshness (exponential decay) — 30%
    pub_date = item.get('pub_date', '') or item.get('published', '')
    freshness = _freshness_score(pub_date)

    # Keyword relevance — 20%
    title = (item.get('cn_title') or item.get('title', '')).lower()
    summary = (item.get('cn_summary') or item.get('summary', '')).lower()
    keywords = _keyword_score(f"{title} {summary}")

    # Source weight — 10%
    source_weight = min(item.get('weight', 1.0) * 20, 100)

    # AIbase mandatory boost
    aibase_boost = 0
    if item.get('source', '') in ('AIbase 头条', 'AIbase', 'aibase'):
        aibase_boost = 15

    composite = (
        signal * 0.40 +
        freshness * 0.30 +
        keywords * 0.20 +
        source_weight * 0.10
    ) + aibase_boost

    return round(composite, 1)


def assign_signal_grade(score: float) -> str:
    """Map score to signal grade label."""
    if score >= GRADE_HEAVY:
        return '重磅'
    elif score >= GRADE_NOTICEABLE:
        return '值得关注'
    return '常规'


def rank_items(items: List[Dict]) -> List[Dict]:
    """Rank items by composite score, descending. Adds _score and signal_grade."""
    for item in items:
        item['_score'] = calculate_item_score(item)
        item['signal_grade'] = assign_signal_grade(item['_score'])
    items.sort(key=lambda x: x.get('_score', 0), reverse=True)
    return items


def select_top_items(items: List[Dict], max_items: int = 35,
                     max_per_source: int = 5,
                     max_per_category: Dict = None) -> List[Dict]:
    """Select top items with source/category diversity constraints."""
    ranked = rank_items(items)

    selected = []
    source_counts = {}
    category_counts = {}

    max_per_category = max_per_category or {}

    # Always include AIbase top items first
    aibase_items = [i for i in ranked if i.get('source', '') in ('AIbase 头条', 'AIbase', 'aibase')]
    for item in aibase_items[:5]:
        source = item.get('source', 'Unknown')
        category = item.get('category', 'headline')
        selected.append(item)
        source_counts[source] = source_counts.get(source, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1

    for item in ranked:
        if item in selected:
            continue

        source = item.get('source', 'Unknown')
        category = item.get('category', 'headline')

        if source_counts.get(source, 0) >= max_per_source:
            continue

        cat_limit = max_per_category.get(category, 99)
        if category_counts.get(category, 0) >= cat_limit:
            continue

        selected.append(item)
        source_counts[source] = source_counts.get(source, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1

        if len(selected) >= max_items:
            break

    return selected
