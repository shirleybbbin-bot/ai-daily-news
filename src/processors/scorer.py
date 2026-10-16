"""News scoring and ranking system.
Weighted scoring (2026-10-04 rebalanced):
  source_signal 25% + freshness 25% (exponential decay, 12h half-life) +
  keyword_relevance 15% + industry_impact 25% + source_weight 10%

signal 为 0（非 HN/GitHub 来源）时，其 25% 权重按比例摊回其余四项，避免普通新闻被系统性压低。
Also adds signal_grade field: 重磅 / 值得关注 / 常规
"""
import math
from typing import Dict, List
from datetime import datetime, timezone


# === Signal grade thresholds ===
# 绝对阈值：仅作参考/兜底，实际分级按排名分位（见 assign_grades_by_rank）
GRADE_HEAVY = 55.0       # score >= 55 → 重磅
GRADE_NOTICEABLE = 25.0  # score >= 25 → 值得关注
# score < 25 → 常规

# === 综合打分权重（五项，和为 1.0）===
# 2026-10-04 调整：原四项里 source_signal 独占 40% 且极易饱和，
# 导致 HN 社区热帖（哪怕只是个人 hobby 项目）稳定压过真正的行业新闻。
# 现拆出 impact（行业影响力）单独占 25%，并下调 signal。
W_SIGNAL = 0.25         # 社区热度（HN 分 / GitHub stars）
W_FRESHNESS = 0.25      # 时效
W_KEYWORD = 0.15        # AI 关键词密度
W_IMPACT = 0.25         # 行业影响力（主体量级 / 是否个人 demo）
W_SOURCE = 0.10         # 来源权重

# === 行业影响力：主体名单 ===
# 出现在标题/摘要里即视为"行业级事件"，区别于个人项目
MAJOR_ENTITIES = [
    'openai', 'anthropic', 'deepmind', 'google', 'microsoft', 'nvidia',
    'meta', 'apple', 'amazon', 'deepseek', 'mistral', 'xai', 'perplexity',
    '阿里', '字节', '腾讯', '百度', '华为', '月之暗面', '智谱', '商汤',
    '科大讯飞', '小米', '英伟达', 'openrouter', 'huggingface', 'stability',
    'adobe', 'samsung', 'intel', 'amd', 'qualcomm',
]
# 个人/hobby 项目标记词：命中即大幅降权
HOBBY_MARKERS = [
    'show hn', 'i built', 'i made', 'made an', 'built a', 'my weekend',
    '我用', '我做了', '我写了个', '自制', '周末项目', '玩具项目',
]

# 排名分位：绝对分数会随当日新闻质量整体漂移（实测某日全部落在 26~44，
# 用 55/25 切会让 35 条全部挤进同一档），改按排名比例切分，保证每天都有三层层次。
GRADE_HEAVY_RATIO = 0.15        # 前 15% → 重磅
GRADE_NOTICEABLE_RATIO = 0.55   # 前 55%（含重磅）→ 值得关注，其余 → 常规


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


# HN 热度的合理区间（log2 刻度）。低于 FLOOR 视为无热度，达到 CEIL 视为顶格。
# 旧实现 min(log2*12, 60) 在 HN 分数≥32 时即饱和，139 分和 401 分拿同样的 60，
# 社区热度因此变成了一个"是不是 HN"的二值开关，而不是真实热度。
HN_FLOOR = 16.0
HN_CEIL = 1024.0


def _source_signal_score(item: Dict) -> float:
    """Score based on engagement signals (HN score, GitHub stars). Returns 0–100."""
    score = 0.0

    hn_score = item.get('hn_score', 0)
    if hn_score > 0:
        # 在 [HN_FLOOR, HN_CEIL] 区间内按 log2 线性映射到 0–100，避免过早饱和
        lo, hi = math.log2(HN_FLOOR), math.log2(HN_CEIL)
        val = (math.log2(max(hn_score, 1)) - lo) / (hi - lo) * 100
        score += max(0.0, min(val, 100.0))

    stars_today = item.get('stars_today', 0)
    if stars_today > 0:
        score += min(math.log2(max(stars_today, 1)) * 10, 40)

    return min(score, 100.0)


def _impact_score(item: Dict) -> float:
    """行业影响力代理指标，0–100。

    纯公式近似的启发式，用于抵消社区热度的偏置：
      - 大厂/头部实验室亲自下场 → 加分（行业事件）
      - 个人 hobby/demo 项目（Show HN 等）→ 重罚
      - 政策/监管/融资类 → 天生高影响
    """
    title = (item.get('cn_title') or item.get('title', '') or '').lower()
    summary = (item.get('cn_summary') or item.get('summary', '') or '').lower()
    text = f"{title} {summary}"
    category = item.get('category', '') or ''

    score = 30.0  # 基线

    # 1) 主体量级
    hits = sum(1 for e in MAJOR_ENTITIES if e in text)
    if hits:
        score += min(25.0, 15.0 + hits * 5.0)

    # 2) 高影响类别
    if category in ('bigtech', 'policy', 'headline'):
        score += 12.0
    elif category in ('product', 'funding', '融资'):
        score += 8.0

    # 3) hobby/demo 重罚
    if any(m in text for m in HOBBY_MARKERS):
        score -= 35.0
    # HN 开源项目且无头部主体 → 大概率是个人作品
    elif item.get('hn_score', 0) > 0 and category == 'opensource' and not hits:
        score -= 20.0

    return max(0.0, min(score, 100.0))


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

    # 行业影响力 — 25%
    impact = _impact_score(item)

    if signal > 0:
        composite = (
            signal * W_SIGNAL +
            freshness * W_FRESHNESS +
            keywords * W_KEYWORD +
            impact * W_IMPACT +
            source_weight * W_SOURCE
        )
    else:
        # signal 只对 HN 热帖 / GitHub 项目有值，普通 RSS 文章恒为 0。
        # 若直接按原权重算，普通新闻上限被压掉 25%，永远够不到 GRADE_HEAVY，
        # 导致"重磅"恒为 0 条。这里把 W_SIGNAL 的空白权重按比例摊回其余四项（归一化）。
        rest = W_FRESHNESS + W_KEYWORD + W_IMPACT + W_SOURCE
        composite = (
            freshness * W_FRESHNESS +
            keywords * W_KEYWORD +
            impact * W_IMPACT +
            source_weight * W_SOURCE
        ) / rest

    return round(composite + aibase_boost, 1)


def assign_signal_grade(score: float) -> str:
    """Map score to signal grade label."""
    if score >= GRADE_HEAVY:
        return '重磅'
    elif score >= GRADE_NOTICEABLE:
        return '值得关注'
    return '常规'


def assign_grades_by_rank(items: List[Dict]) -> None:
    """按排名分位就地打标（items 必须已按分数降序）。保证每天都有三层层次。"""
    n = len(items)
    if n == 0:
        return
    heavy_cut = max(1, int(round(n * GRADE_HEAVY_RATIO)))
    notice_cut = max(heavy_cut + 1, int(round(n * GRADE_NOTICEABLE_RATIO)))
    for idx, item in enumerate(items):
        if idx < heavy_cut:
            item['signal_grade'] = '重磅'
        elif idx < notice_cut:
            item['signal_grade'] = '值得关注'
        else:
            item['signal_grade'] = '常规'


def rank_items(items: List[Dict]) -> List[Dict]:
    """Rank items by composite score, descending. Adds _score and signal_grade."""
    for item in items:
        item['_score'] = calculate_item_score(item)
        item['signal_grade'] = assign_signal_grade(item['_score'])
    items.sort(key=lambda x: x.get('_score', 0), reverse=True)
    # 用排名分位覆盖绝对阈值的结果
    assign_grades_by_rank(items)
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
