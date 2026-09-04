"""Story merger — clusters same-event multi-source reports into one card.

Learned from:
- Thysrael/Horizon: merge repeated stories across platforms BEFORE briefing
- LearnPrompt/ai-news-radar: story folding (多源折叠)
- ai-daily-news: three-layer dedup (URL → title → summary similarity)

Instead of just removing duplicates, this merges them into a single card
with multiple source links and a "多源 N" badge.
"""
from typing import List, Dict, Tuple
from utils.text import similarity


def _extract_key_entities(text: str) -> set:
    """Extract key entity tokens for fast pre-filtering."""
    entities = set()
    # Company/product names commonly appearing in AI news
    known_entities = [
        'openai', 'gpt', 'claude', 'anthropic', 'gemini', 'google', 'meta',
        'apple', 'microsoft', 'nvidia', '英伟达', '百度', '阿里', '腾讯',
        '字节', '华为', 'deepseek', 'mistral', 'moonshot', '月之暗面',
        '智谱', 'glm', '通义', 'qwen', 'perplexity', 'xai', 'grok',
        'stability', 'hugging', 'midjourney', 'stablediffusion',
    ]
    text_lower = text.lower()
    for entity in known_entities:
        if entity in text_lower:
            entities.add(entity)
    return entities


def _are_same_story(item_a: Dict, item_b: Dict, threshold: float = 0.55) -> bool:
    """Determine if two items are about the same event.

    Uses a combination of:
    1. Title similarity (primary signal)
    2. Key entity overlap (fast filter)
    3. Summary similarity (secondary confirmation)
    """
    title_a = (item_a.get('cn_title') or item_a.get('title', '')).lower()
    title_b = (item_b.get('cn_title') or item_b.get('title', '')).lower()

    # Fast path: if titles are very similar, merge
    title_sim = similarity(title_a, title_b)
    if title_sim > threshold:
        return True

    # Entity overlap check — if they share key entities AND have moderate similarity
    entities_a = _extract_key_entities(title_a + ' ' + (item_a.get('cn_summary') or item_a.get('summary', '')).lower())
    entities_b = _extract_key_entities(title_b + ' ' + (item_b.get('cn_summary') or item_b.get('summary', '')).lower())

    shared = entities_a & entities_b
    if len(shared) >= 2 and title_sim > 0.35:
        # Double-check with summary similarity
        summary_a = (item_a.get('cn_summary') or item_a.get('summary', '')).lower()
        summary_b = (item_b.get('cn_summary') or item_b.get('summary', '')).lower()
        summary_sim = similarity(summary_a, summary_b)
        if summary_sim > 0.4:
            return True

    return False


def merge_stories(items: List[Dict], similarity_threshold: float = 0.55) -> Tuple[List[Dict], Dict]:
    """Merge same-event items from different sources into single cards.

    Returns (merged_items, stats).
    Each merged item gets:
    - sources: list of {source, url} for all merged items
    - source_count: number of merged sources
    - merged: True if this item absorbed others
    """
    if not items:
        return [], {'merged_groups': 0, 'items_before': 0, 'items_after': 0}

    stats = {
        'merged_groups': 0,
        'items_before': len(items),
        'items_after': 0,
    }

    # Build clusters using union-find approach
    clusters = []  # each cluster is a list of item indices
    assigned = [False] * len(items)

    for i, item in enumerate(items):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(items)):
            if assigned[j]:
                continue
            # Check against any member of the cluster
            for k in cluster:
                if _are_same_story(items[k], items[j], similarity_threshold):
                    cluster.append(j)
                    assigned[j] = True
                    break
        clusters.append(cluster)

    # Merge each cluster into one item
    merged_items = []
    for cluster in clusters:
        if len(cluster) == 1:
            merged_items.append(items[cluster[0]])
        else:
            # Pick the best item as the primary (highest score, or first if no score)
            cluster_items = [items[idx] for idx in cluster]
            cluster_items.sort(key=lambda x: x.get('_score', 0), reverse=True)
            primary = cluster_items[0].copy()

            # Collect all sources
            all_sources = []
            for ci in cluster_items:
                all_sources.append({
                    'source': ci.get('source', ''),
                    'url': ci.get('url', ''),
                    'title': ci.get('cn_title') or ci.get('title', ''),
                })

            primary['sources'] = all_sources
            primary['source_count'] = len(all_sources)
            primary['merged'] = True

            # Use the best summary across sources (longest one usually has most info)
            best_summary = primary.get('cn_summary', '')
            for ci in cluster_items[1:]:
                ci_summary = ci.get('cn_summary', '')
                if len(ci_summary) > len(best_summary):
                    best_summary = ci_summary
            primary['cn_summary'] = best_summary

            stats['merged_groups'] += 1
            merged_items.append(primary)

    stats['items_after'] = len(merged_items)

    removed = stats['items_before'] - stats['items_after']
    print(f"  [StoryMerge] {stats['items_before']} → {stats['items_after']} "
          f"(merged {stats['merged_groups']} groups, removed {removed} duplicates)")

    return merged_items, stats


if __name__ == '__main__':
    # Quick test with mock data
    test_items = [
        {'cn_title': 'OpenAI发布GPT-5', 'cn_summary': 'OpenAI今日发布了GPT-5模型，性能大幅提升', 'source': 'AIbase', 'url': 'http://a.com', '_score': 80},
        {'cn_title': 'OpenAI推出GPT-5大模型', 'cn_summary': 'OpenAI宣布推出GPT-5，支持多模态推理', 'source': '量子位', 'url': 'http://b.com', '_score': 70},
        {'cn_title': '谷歌发布Gemini 3', 'cn_summary': '谷歌发布Gemini 3模型', 'source': 'IT之家', 'url': 'http://c.com', '_score': 60},
    ]
    merged, stats = merge_stories(test_items)
    print(f"Test: {stats}")
    for m in merged:
        print(f"  {m.get('cn_title')} (sources: {m.get('source_count', 1)})")
