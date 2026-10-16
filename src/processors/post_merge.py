"""Post-summary duplicate detection (semantic, LLM-judged).

Why this exists
---------------
`story_merger.merge_stories()` runs in `process_and_summarize()` BEFORE the
summarization loop (build_daily.py:140 vs :146). At that point `cn_title` and
`cn_summary` are still empty, so it can only compare the *original English*
titles. Consequences observed in production:

- Cross-source reports of the same event are not merged (e.g. 2026-10-06:
  #2 diff.wikimedia.org vs #3 The Verge on OpenAI rogue wiki agents;
  #11 TechCrunch vs #17 reflection.ai blog on Reflection Beam).
- The `len(shared_entities) >= 2` gate in `_are_same_story` almost never fires
  outside bigtech, because `known_entities` is a hardcoded 28-name list that
  lacks Wikimedia/Reflection/Beam/etc.

Empirically calibrated on 2026-10-06 data: statistical pre-filtering is
unusable — shared-entity>=2 yields 6 false positives vs 1 true positive, and the
true pair #2/#3 (shared=1, title bigram Jaccard=0.23) is indistinguishable from
~40 unrelated OpenAI pairs. So we skip heuristics and let the LLM judge
semantically, in ONE batched call over the whole digest.

Cost: 1 LLM call per run. Runs after summarization, before calibration.
"""
import os
from typing import List, Dict, Tuple, Optional

# This repo puts both repo-root and src/ on sys.path (build_daily.py:20-23), so
# `processors.summarizer` and `src.processors.summarizer` are both valid depending
# on how the module is imported. Support both.
try:
    from processors.summarizer import llm_chat, parse_llm_json
except ModuleNotFoundError:  # pragma: no cover
    from src.processors.summarizer import llm_chat, parse_llm_json

DETECT_SYSTEM = """你是资深科技媒体主编，负责在日报发稿前排查"同一事件被多家媒体重复报道"的情况。

判断标准（严格）：
1. 只有两条讲的是**同一个具体事件/同一条具体新闻**时才判为重复。主体相同但事件不同，不算重复。
2. 以下情况**不算**重复：
   - 同一家公司（如 OpenAI）的两条不同新闻（一条发布产品、一条卷入诉讼）
   - 同一主题的评论文章 vs 事实报道
   - 一篇是某事件的报道，另一篇只是顺带提及该事件
3. 以下情况**算**重复：
   - 多家媒体报道同一件事，只是措辞/角度/详略不同
   - 同一产品的发布，一条来自官方博客、一条来自新闻媒体

输出严格 JSON，不要任何解释文字、不要 markdown 围栏：
{"groups": [[2, 3], [11, 17]]}
其中数字是条目的编号（从 1 开始，与输入编号一致）。每组 2 个或以上编号。
如果没有任何重复，输出：{"groups": []}
宁可漏判，不要误判。拿不准就不合并。"""


def _build_digest_listing(items: List[Dict]) -> str:
    """Compact listing of items for the LLM judge (index, title, summary head)."""
    lines = []
    for i, it in enumerate(items, 1):
        title = (it.get('cn_title') or it.get('title') or '').strip()
        summary = (it.get('cn_summary') or it.get('summary') or '').strip()
        # 60 chars of summary is enough to identify the event, keeps prompt small
        lines.append(f"#{i} [{it.get('source', '')}] {title}\n    {summary[:60]}")
    return "\n".join(lines)


def detect_duplicate_groups(items: List[Dict]) -> List[List[int]]:
    """Ask the LLM which items are the same story. Returns 1-based index groups."""
    if len(items) < 2:
        return []

    user_prompt = (
        f"以下是今天日报的 {len(items)} 条候选，请找出讲同一事件的一组组条目。\n\n"
        f"{_build_digest_listing(items)}\n\n"
        "只输出 JSON。"
    )

    # Retry: this is a single-shot call with no per-item fallback, so one
    # malformed response silently disables dedup for the whole day (observed
    # 2026-10-08: model returned unparsable text, 4 duplicate cards stayed in
    # the digest; re-running the identical prompt returned the correct
    # {"groups": [[1,4,16],[12,19]]}). Give it a few attempts before giving up.
    max_attempts = int(os.environ.get('POST_MERGE_ATTEMPTS', '3'))
    data = None
    for attempt in range(1, max_attempts + 1):
        result = llm_chat(DETECT_SYSTEM, user_prompt, temperature=0.1)
        if not result:
            print(f"  [PostMerge] LLM returned empty (attempt {attempt}/{max_attempts})")
            continue
        parsed = parse_llm_json(result)
        if isinstance(parsed, dict):
            data = parsed
            break
        print(f"  [PostMerge] LLM JSON unparsable (attempt {attempt}/{max_attempts}), retrying")

    if data is None:
        print("  [PostMerge] LLM unavailable/unparsable after retries, skip")
        return []

    raw_groups = data.get('groups') or []
    if not isinstance(raw_groups, list):
        return []

    n = len(items)
    groups: List[List[int]] = []
    seen = set()
    for g in raw_groups:
        if not isinstance(g, list) or len(g) < 2:
            continue
        idxs = []
        for x in g:
            try:
                xi = int(x)
            except (TypeError, ValueError):
                continue
            # Validate range, drop already-claimed, keep 1-based
            if 1 <= xi <= n and xi not in seen:
                idxs.append(xi)
        if len(idxs) >= 2:
            groups.append(sorted(idxs))
            seen.update(idxs)

    return groups


def merge_groups(items: List[Dict], groups: List[List[int]]) -> Tuple[List[Dict], Dict]:
    """Merge 1-based index groups, keeping the highest-scoring item as primary."""
    stats = {'groups': 0, 'items_before': len(items), 'items_after': len(items)}
    if not groups:
        return items, stats

    drop_idx = set()
    # position -> card, so a merged card inherits the slot of its best-ranked
    # member instead of being appended at the end (which would push a 重磅
    # story to the bottom of the digest).
    placed = {}
    for g in groups:
        cluster = [items[i - 1] for i in g]
        cluster.sort(key=lambda x: x.get('_score', 0), reverse=True)
        primary = cluster[0].copy()

        all_sources = []
        for ci in cluster:
            all_sources.append({
                'source': ci.get('source', ''),
                'url': ci.get('url', ''),
                'title': ci.get('cn_title') or ci.get('title', ''),
            })
        primary['sources'] = all_sources
        primary['source_count'] = len(all_sources)
        primary['merged'] = True

        # Keep the most informative (longest) summary across the cluster
        best = primary.get('cn_summary', '') or ''
        for ci in cluster[1:]:
            s = ci.get('cn_summary', '') or ''
            if len(s) > len(best):
                best = s
        primary['cn_summary'] = best

        placed[min(g)] = primary
        drop_idx.update(g)
        for i in g:
            print(f"  [PostMerge] 合并 #{i} {items[i-1].get('cn_title','')[:26]}")

    out = []
    for i, it in enumerate(items, 1):
        if i in drop_idx:
            # Emit the merged card at the position of its first member
            if i in placed:
                out.append(placed[i])
            continue
        out.append(it)

    stats['groups'] = len(groups)
    stats['items_after'] = len(out)
    print(f"  [PostMerge] {stats['items_before']} → {stats['items_after']} "
          f"(merged {stats['groups']} groups)")
    return out, stats


def post_summarize_merge(items: List[Dict]) -> Tuple[List[Dict], Dict]:
    """Full pass: detect duplicate stories post-summarization and merge them."""
    groups = detect_duplicate_groups(items)
    if not groups:
        print("  [PostMerge] no duplicate stories detected")
        return items, {'groups': 0, 'items_before': len(items), 'items_after': len(items)}
    return merge_groups(items, groups)
