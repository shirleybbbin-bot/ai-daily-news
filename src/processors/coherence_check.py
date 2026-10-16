"""Coherence self-check + repair — re-reads each summary against its source text.

Learned from SeanLF/claude-rss-news-digest:
  CLUSTER → RECAP → SELECT → WRITE → COHERENCE
  After summarization, run a COHERENCE pass that re-reads each summary
  against its source text. If it fails (claims not supported by source),
  attempt one repair regeneration, then re-check. Drop if still fails.

This is different from calibrator (which checks style/formatting) and
fact_checker (which checks URL accessibility + entity matching).
Coherence check verifies that the LLM didn't hallucinate or fabricate.
"""
import json
import re
from typing import Dict, List, Tuple
from processors.summarizer import llm_chat
from utils.text import trim_title, truncate_text


COHERENCE_SYSTEM = """你是一个事实核查员，负责检查AI新闻摘要是否忠于原文。

你会收到：
1. 原文（或原文片段）
2. 生成的中文摘要

你的任务：
1. 逐句检查摘要中的每个事实性陈述，确认它在原文中有依据
2. 检查是否有"加戏"——摘要中出现了原文没有的信息（如无中生有的数字、公司名、因果关系）
3. 检查是否有"歪曲"——原文说的是A，摘要写成了B
4. 检查是否有"遗漏关键限定"——原文说"可能在2026年"，摘要写成了"2026年将"

判断结果：
- pass: 摘要忠实于原文，无问题
- repair: 存在少量不一致，需要修正（给出修正后的摘要）
- fail: 严重不一致，无法通过简单修正修复

请严格按JSON格式输出。"""

COHERENCE_USER_TEMPLATE = """请检查以下摘要是否忠于原文：

【原文】
{source_text}

【生成的摘要】
标题：{cn_title}
摘要：{cn_summary}

请输出JSON：
{{
  "verdict": "pass" 或 "repair" 或 "fail",
  "issues": ["问题1", "问题2"],
  "repaired_title": "修正后的标题（如verdict=repair则必填）",
  "repaired_summary": "修正后的摘要（如verdict=repair则必填，90-160字）",
  "confidence": 0.0-1.0
}}"""


def _parse_json_loose(text: str):
    """宽松解析 LLM 输出。

    模型有时会在 JSON 之后追加解释文字，直接 json.loads 会抛
    "Extra data" 而白白丢掉一次已经完成的有效核查（实测遇到过）。
    这里退化一步：扫描并取出第一个完整可解析的 JSON 对象。
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    idx = text.find('{')
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            return obj
        except json.JSONDecodeError:
            idx = text.find('{', idx + 1)
    return None


def check_coherence(item: Dict) -> Dict:
    """Check if a summary is coherent with its source text.

    Returns the item with a 'coherence' field containing the check result.
    If repair is needed and succeeds, updates the summary in-place.
    """
    source_text = item.get('summary', '') or item.get('raw_summary', '')
    cn_title = item.get('cn_title', '')
    cn_summary = item.get('cn_summary', '')

    if not source_text or not cn_summary:
        item['coherence'] = {'verdict': 'skip', 'reason': 'no source text'}
        return item

    # Truncate source to fit context window
    source_text = source_text[:800]

    user_prompt = COHERENCE_USER_TEMPLATE.format(
        source_text=source_text,
        cn_title=cn_title,
        cn_summary=cn_summary,
    )

    result = llm_chat(COHERENCE_SYSTEM, user_prompt, temperature=0.1)

    if not result:
        item['coherence'] = {'verdict': 'skip', 'reason': 'LLM call failed'}
        return item

    try:
        clean = re.sub(r'```json\s*', '', result)
        clean = re.sub(r'```\s*', '', clean).strip()
        parsed = _parse_json_loose(clean)
        if parsed is None:
            item['coherence'] = {'verdict': 'skip', 'reason': 'unparsable response'}
            return item

        verdict = parsed.get('verdict', 'pass')
        coherence_info = {
            'verdict': verdict,
            'issues': parsed.get('issues', []),
            'confidence': parsed.get('confidence', 0.0),
        }

        if verdict == 'repair':
            repaired_title = parsed.get('repaired_title', '')
            repaired_summary = parsed.get('repaired_summary', '')

            if repaired_summary and len(repaired_summary) >= 80:
                item['cn_title'] = trim_title(repaired_title) if repaired_title else cn_title
                item['cn_summary'] = truncate_text(repaired_summary, 160)
                coherence_info['repaired'] = True
            else:
                # Repair was insufficient, mark as fail
                coherence_info['verdict'] = 'fail'
                coherence_info['repaired'] = False

        item['coherence'] = coherence_info
        return item

    except (json.JSONDecodeError, KeyError) as e:
        item['coherence'] = {'verdict': 'skip', 'reason': f'parse error: {e}'}
        return item


def drop_failed_items(items: List[Dict], min_keep: int = 25) -> Tuple[List[Dict], Dict]:
    """剔除一致性 fail 的条目 —— 改不回来的"加戏"不该流到稿子里。

    兜底：如果剔除后不足 min_keep 条，按分数从高到低保留若干 fail 条目补足，
    避免某天 LLM 大面积误判时稿子直接空掉（宁可留着待人工核，也好过没内容）。
    """
    stats = {'checked': len(items), 'dropped': 0, 'kept_fail': 0}

    good = [i for i in items if i.get('coherence', {}).get('verdict') != 'fail']
    bad = [i for i in items if i.get('coherence', {}).get('verdict') == 'fail']
    if not bad:
        stats['remaining'] = len(good)
        return good, stats

    if len(good) >= min_keep:
        stats['dropped'] = len(bad)
        stats['remaining'] = len(good)
        return good, stats

    # 不够 min_keep 条：按分数补足（items 此刻已是分数降序）
    need = min_keep - len(good)
    ranked_bad = sorted(bad, key=lambda x: x.get('_score', 0), reverse=True)
    stats['kept_fail'] = min(need, len(ranked_bad))
    stats['dropped'] = len(bad) - stats['kept_fail']
    kept = good + ranked_bad[:need]
    stats['remaining'] = len(kept)
    return kept, stats


def coherence_batch(items: List[Dict]) -> Tuple[List[Dict], Dict]:
    """Run coherence check on all items. Returns (checked_items, stats)."""
    print("\n🔍 === 一致性自检（Coherence Check）===")
    print(f"  核查维度: 摘要是否忠于原文 | 有无加戏/歪曲/遗漏限定")

    checked = []
    stats = {
        'total': len(items),
        'pass': 0,
        'repaired': 0,
        'failed': 0,
        'skipped': 0,
        'issues': [],
    }

    for i, item in enumerate(items):
        title = item.get('cn_title', '')[:25]
        try:
            result = check_coherence(item)
            coherence = result.get('coherence', {})
            verdict = coherence.get('verdict', 'skip')

            if verdict == 'pass':
                stats['pass'] += 1
                icon = '✅'
            elif verdict == 'repair':
                if coherence.get('repaired'):
                    stats['repaired'] += 1
                    icon = '🔧'
                else:
                    stats['failed'] += 1
                    icon = '❌'
            elif verdict == 'fail':
                stats['failed'] += 1
                icon = '❌'
                stats['issues'].append({
                    'title': title,
                    'issues': coherence.get('issues', []),
                })
            else:
                stats['skipped'] += 1
                icon = '⏭️'

            print(f"  [{icon}] ({i+1}/{len(items)}) {title}...")
            if coherence.get('issues'):
                for issue in coherence['issues'][:2]:
                    print(f"       → {issue}")

            checked.append(result)
        except Exception as e:
            print(f"  [💥] ({i+1}/{len(items)}) {title}... → {e}")
            checked.append(item)
            stats['failed'] += 1

    print(f"\n  📊 一致性结果: ✅{stats['pass']} 🔧{stats['repaired']} "
          f"❌{stats['failed']} ⏭️{stats['skipped']}")

    if stats['issues']:
        print(f"  🚩 严重不一致 ({len(stats['issues'])}项):")
        for issue in stats['issues'][:5]:
            print(f"    • {issue['title']}... → {issue['issues'][0] if issue['issues'] else 'unknown'}")

    return checked, stats


if __name__ == '__main__':
    # Quick test
    test_item = {
        'cn_title': 'OpenAI发布GPT-5',
        'cn_summary': 'OpenAI发布了GPT-5模型，支持多模态推理，性能比GPT-4提升50%。该模型免费开放给所有用户使用。',
        'summary': 'OpenAI announced GPT-5, a new multimodal model with improved reasoning capabilities. The model is available to Plus users.',
    }
    result = check_coherence(test_item)
    print(json.dumps(result.get('coherence', {}), ensure_ascii=False, indent=2))
