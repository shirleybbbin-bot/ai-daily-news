"""Self-calibration mechanism: LLM second-pass quality check and correction.
Ensures summaries are accurate, professional, and properly formatted."""
import json
import re
from typing import Dict, List, Tuple
from processors.summarizer import llm_chat
from utils.text import trim_title, truncate_text


CALIBRATE_SYSTEM = """你是AI资讯日报的质检编辑（校准员），负责对已生成的新闻摘要进行二次审核。
你的职责：
1. **准确性校验**：摘要内容是否忠于原文？有无捏造、夸大或误导性信息？
2. **完整性校验**：摘要是否遗漏了关键信息（如公司名、产品名、关键数字）？
3. **语言规范**：是否符合中文表达习惯？有无翻译腔？专业术语使用是否准确？
4. **AI腔检测（关键）**：摘要是否有AI生成的痕迹？重点检查并修正：
   - 出现"此举旨在""标志着""被视为""引发业界""值得关注的是""据悉""值得一提的是""不仅…而且"等套话 → 必须重写
   - 出现"赋能""助力""打造""引领""重塑"等公关稿高频词 → 替换为具体描述
   - 句式单调（如每条都是"XX发布XX，具有XX特点"）→ 变换句式
   - 过于书面化 → 改为更口语化、更像记者写的表达
5. **字数控制**：摘要是否在90-160字范围内？过长需精简，过短需补充。
6. **分类准确性**：分类是否恰当？有无误分类？
7. **标签质量**：标签是否准确且不多于3个？

如果发现问题，请给出修正后的版本。如果没问题，原样返回。
请严格按JSON格式输出，不要有多余文字。"""

CALIBRATE_USER_TEMPLATE = """请审核并修正以下新闻摘要：

原始标题：{raw_title}
中文标题：{cn_title}
中文摘要：{cn_summary}
标签：{tags}
分类：{category}
来源：{source}

请输出修正后的JSON（如无修改则原样输出）：
{{
  "cn_title": "修正后的中文标题，20-28字，必须是完整短句、不得截断",
  "cn_summary": "修正后的中文摘要，90-160字",
  "tags": ["标签1", "标签2", "标签3"],
  "category": "分类key",
  "issues_found": ["发现的问题1", "发现的问题2"],
  "corrected": true或false
}}"""


def calibrate_item(item: Dict) -> Dict:
    """Run LLM calibration on a single item. Returns corrected item + calibration info."""
    if not item.get('cn_summary'):
        return item

    user_prompt = CALIBRATE_USER_TEMPLATE.format(
        raw_title=item.get('title', ''),
        cn_title=item.get('cn_title', ''),
        cn_summary=item.get('cn_summary', ''),
        tags=json.dumps(item.get('tags', []), ensure_ascii=False),
        category=item.get('category', ''),
        source=item.get('source', ''),
    )

    result = llm_chat(CALIBRATE_SYSTEM, user_prompt, temperature=0.1)

    if not result:
        return item

    try:
        clean = re.sub(r'```json\s*', '', result)
        clean = re.sub(r'```\s*', '', clean).strip()
        parsed = json.loads(clean)

        if parsed.get('corrected', False):
            item['cn_title'] = trim_title(parsed.get('cn_title', item['cn_title']))
            item['cn_summary'] = truncate_text(parsed.get('cn_summary', item['cn_summary']), 160)
            item['tags'] = parsed.get('tags', item.get('tags', []))[:3]
            if parsed.get('category'):
                item['category'] = parsed['category']
            item['calibration_issues'] = parsed.get('issues_found', [])
            return item
        else:
            item['calibration_issues'] = []
            return item
    except (json.JSONDecodeError, KeyError):
        return item


def calibrate_batch(items: List[Dict]) -> Tuple[List[Dict], Dict]:
    """Run calibration on all items. Returns calibrated items + summary stats."""
    print("\n🔍 === 自校准机制 ===")
    calibrated = []
    stats = {
        'total': len(items),
        'corrected': 0,
        'issues': [],
    }

    for i, item in enumerate(items):
        print(f"  [{i+1}/{len(items)}] Calibrating: {item.get('cn_title', '')[:30]}...")
        try:
            result = calibrate_item(item)
            if result.get('calibration_issues'):
                stats['corrected'] += 1
                for issue in result['calibration_issues']:
                    stats['issues'].append({
                        'title': result.get('cn_title', ''),
                        'issue': issue,
                    })
            calibrated.append(result)
        except Exception as e:
            print(f"  [!] Calibration error: {e}")
            calibrated.append(item)

    print(f"  📊 校准完成: {stats['corrected']}/{stats['total']} 条被修正")
    if stats['issues']:
        print(f"  📝 发现的问题:")
        for issue in stats['issues'][:5]:
            print(f"    • {issue['title'][:15]}...: {issue['issue']}")

    return calibrated, stats


PAPER_CALIBRATE_SYSTEM = """你是AI学术编辑的质检员，负责审核论文解读的准确性。
重点检查：
1. 论文中文标题是否准确反映原文？
2. 一句话总结是否抓住了论文核心？
3. 核心要点是否正确且无遗漏？
4. 通俗解读是否让非专业人士能理解？有无过度简化导致误导？
5. "为什么值得关注"是否有说服力？

请严格按JSON格式输出。"""

PAPER_CALIBRATE_USER = """请审核以下论文解读：

原论文标题：{raw_title}
中文标题：{cn_title}
一句话总结：{one_liner}
核心要点：{key_points}
通俗解读：{plain_explanation}
为什么值得关注：{why_care}
综合摘要：{cn_summary}

请输出修正后的JSON（如无修改则原样返回）：
{{
  "cn_title": "修正后的中文标题",
  "one_liner": "修正后的一句话总结",
  "key_points": ["要点1", "要点2", "要点3"],
  "plain_explanation": "修正后的通俗解读",
  "why_care": "修正后的为什么值得关注",
  "cn_summary": "修正后的综合摘要",
  "issues_found": ["问题1"],
  "corrected": true或false
}}"""


def calibrate_paper(item: Dict) -> Dict:
    """Run LLM calibration on a paper item."""
    if not item.get('plain_explanation'):
        return calibrate_item(item)

    user_prompt = PAPER_CALIBRATE_USER.format(
        raw_title=item.get('title', ''),
        cn_title=item.get('cn_title', ''),
        one_liner=item.get('one_liner', ''),
        key_points=json.dumps(item.get('key_points', []), ensure_ascii=False),
        plain_explanation=item.get('plain_explanation', ''),
        why_care=item.get('why_care', ''),
        cn_summary=item.get('cn_summary', ''),
    )

    result = llm_chat(PAPER_CALIBRATE_SYSTEM, user_prompt, temperature=0.1)

    if not result:
        return item

    try:
        clean = re.sub(r'```json\s*', '', result)
        clean = re.sub(r'```\s*', '', clean).strip()
        parsed = json.loads(clean)

        if parsed.get('corrected', False):
            item['cn_title'] = trim_title(parsed.get('cn_title', item['cn_title']))
            item['one_liner'] = parsed.get('one_liner', item.get('one_liner', ''))
            item['key_points'] = parsed.get('key_points', item.get('key_points', []))
            item['plain_explanation'] = parsed.get('plain_explanation', item.get('plain_explanation', ''))
            item['why_care'] = parsed.get('why_care', item.get('why_care', ''))
            item['cn_summary'] = truncate_text(parsed.get('cn_summary', item['cn_summary']), 160)
            item['calibration_issues'] = parsed.get('issues_found', [])
        return item
    except (json.JSONDecodeError, KeyError):
        return item
