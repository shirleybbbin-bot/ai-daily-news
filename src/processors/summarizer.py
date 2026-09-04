"""LLM-powered summarization using GLM (ZhiPu) API.
Enhanced with multi-step prompt strategy learned from CloudFlare-AI-Insight-Daily."""
import os
import json
import re
from typing import Dict, List, Optional
from openai import OpenAI


def get_llm_client() -> OpenAI:
    """Create OpenAI-compatible client for GLM API."""
    return OpenAI(
        api_key=os.environ.get('LLM_API_KEY', ''),
        base_url=os.environ.get('LLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4'),
    )


def get_model() -> str:
    return os.environ.get('LLM_MODEL', 'glm-4.5')


def llm_chat(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
    """Call LLM and return text response."""
    client = get_llm_client()
    try:
        resp = client.chat.completions.create(
            model=get_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=4096,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [!] LLM call failed: {e}")
        return ""


# === Enhanced System Prompts (learned from CloudFlare project) ===

SUMMARIZE_SYSTEM = """你是一名资深AI科技记者，在科技媒体干了十年，刚跳出来自己做公众号"AI洞察局"。
你不是AI，你是人。你写的东西要像人写的。

你的写作原则：
1. **忠于原文**：摘要必须严格来源于原文，不捏造、不歪曲、不添加原文没有的信息。
2. **说人话**：像跟同事聊天一样写，不要用书面套话。把"此举旨在"换成"目的是"，把"被视为"换成"可以看作"，但更好的方式是直接说出来。
3. **信息密度高**：90-160字塞进最多的有效信息——谁做了什么、多少钱、什么效果、什么影响。少用形容词，多用事实。
4. **句式要活**：不要每条都"XX公司发布XX，具有XX特点，将用于XX领域"这个套路。可以倒装、可以短句、可以设问、可以一句话开头。
5. **技术要准**：公司名、产品名、技术术语用中文规范名，不确定的保留英文原文。

绝对禁止出现的AI腔（出现任何一个就重写）：
- "此举旨在" "标志着" "被视为" "引发业界" "值得关注的是" "具有重要意义"
- "据悉" "值得一提的是" "不仅…而且" "一方面…另一方面"
- 以"近日，"开头
- 以"该产品/该技术/该公司"开头的句式
- 任何"赋能""助力""打造""引领"等公关稿高频词

标题要求：
- 20字以内，把最关键的信息放前面
- 不用"震惊""重磅""突破"等夸张词
- 英文标题中文化，但保留品牌名英文（如GPT、Claude）
- 好标题例子："OpenAI开放GPT-5 API，开发者免费调用3个月"
- 差标题例子："OpenAI又有大动作！GPT-5重磅来袭"

任务：
1. 将标题翻译为中文（英文标题），控制在20字以内
2. 将摘要改写为90-160字的中文摘要
3. 为每条新闻生成2-3个标签（如：#OpenAI #产品发布 #大模型）
4. 判断该条目所属的分类

分类选项：headline(今日头条)、investment(投融资)、policy(政策监管)、technology(技术突破)、
product(产品发布)、bigtech(大厂动态)、opinion(大V观点)、paper(论文精选)、opensource(开源热榜)、event(活动预告)

请严格按JSON格式输出，不要有多余文字。"""

SUMMARIZE_USER_TEMPLATE = """请处理以下新闻条目：

标题：{title}
原文摘要：{summary}
来源：{source}
链接：{url}
发布时间：{pub_date}
{paper_info}

请输出JSON：
{{
  "cn_title": "中文标题，20字以内，吸睛但不夸大",
  "cn_summary": "中文摘要，90-160字，信息密度高，包含关键信息",
  "tags": ["标签1", "标签2", "标签3"],
  "category": "分类key"
}}"""


def summarize_item(item: Dict) -> Dict:
    """Use LLM to generate Chinese title, summary, tags, and category for a news item."""
    paper_info = ""
    if item.get('is_paper'):
        authors = ', '.join(item.get('authors', [])[:3])
        paper_info = f"\n论文作者：{authors}\n论文分类：{item.get('arxiv_category', '')}\nPDF链接：{item.get('pdf_url', '')}\n注意：论文需要中文标题、一句话总结、核心要点、通俗解读、为什么值得关注"

    if item.get('is_github_trending'):
        paper_info = f"\nGitHub项目语言：{item.get('repo_language', '')}\n今日新增Star：{item.get('stars_today', 0)}\n总Star：{item.get('total_stars', 0)}\n注意：开源项目需描述项目用途和亮点"

    user_prompt = SUMMARIZE_USER_TEMPLATE.format(
        title=item['title'],
        summary=item.get('summary', '')[:500],
        source=item.get('source', ''),
        url=item.get('url', ''),
        pub_date=item.get('pub_date', ''),
        paper_info=paper_info,
    )

    result = llm_chat(SUMMARIZE_SYSTEM, user_prompt)

    if not result:
        return {
            'cn_title': item['title'][:20],
            'cn_summary': item.get('summary', '')[:160],
            'tags': [],
            'category': item.get('category_hint', '行业动态'),
        }

    try:
        clean = re.sub(r'```json\s*', '', result)
        clean = re.sub(r'```\s*', '', clean).strip()
        parsed = json.loads(clean)
        return {
            'cn_title': parsed.get('cn_title', item['title'])[:20],
            'cn_summary': parsed.get('cn_summary', '')[:200],
            'tags': parsed.get('tags', [])[:3],
            'category': parsed.get('category', 'headline'),
        }
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  [!] Failed to parse LLM response for '{item['title'][:30]}...': {e}")
        return {
            'cn_title': item['title'][:20],
            'cn_summary': result[:160] if result else item.get('summary', '')[:160],
            'tags': [],
            'category': item.get('category_hint', '行业动态'),
        }


PAPER_SYSTEM = """你是一名AI学术编辑，负责为公众号读者解读arXiv论文。

你的核心原则：
1. **忠于论文**：解读内容必须严格基于论文摘要原文，不得捏造论文未提及的方法或结论。
2. **通俗易懂**：用日常语言解释，让非专业读者也能理解。可用类比帮助理解。
3. **深度与准确性并重**：通俗但不失准确，技术细节不能出错。
4. **引人入胜**：让读者明白这篇论文为什么重要，对现实世界有什么影响。

请为论文生成通俗解读，帮助非专业读者理解。
请严格按JSON格式输出，不要有多余文字。"""

PAPER_USER_TEMPLATE = """请解读以下论文：

标题：{title}
摘要：{abstract}
作者：{authors}
分类：{category}
链接：{url}

请输出JSON：
{{
  "cn_title": "论文中文标题，20字以内",
  "one_liner": "一句话总结，30字以内，用通俗语言",
  "key_points": ["核心要点1（20字以内）", "核心要点2", "核心要点3"],
  "plain_explanation": "通俗解读，100-150字，让非专业读者也能理解。用类比和日常语言。",
  "why_care": "为什么值得关注，50-80字。说明对现实世界的影响。",
  "tags": ["标签1", "标签2"],
  "cn_summary": "综合摘要，90-160字，包含论文核心贡献和创新点",
  "category": "paper"
}}"""


def summarize_paper(item: Dict) -> Dict:
    """Special LLM processing for arXiv papers."""
    authors = ', '.join(item.get('authors', [])[:5])
    user_prompt = PAPER_USER_TEMPLATE.format(
        title=item['title'],
        abstract=item.get('summary', '')[:500],
        authors=authors,
        category=item.get('arxiv_category', ''),
        url=item.get('url', ''),
    )

    result = llm_chat(PAPER_SYSTEM, user_prompt, temperature=0.4)

    if not result:
        return summarize_item(item)

    try:
        clean = re.sub(r'```json\s*', '', result)
        clean = re.sub(r'```\s*', '', clean).strip()
        parsed = json.loads(clean)
        return {
            'cn_title': parsed.get('cn_title', item['title'])[:25],
            'cn_summary': parsed.get('cn_summary', '')[:200],
            'tags': parsed.get('tags', [])[:3],
            'category': 'paper',
            'one_liner': parsed.get('one_liner', ''),
            'key_points': parsed.get('key_points', []),
            'plain_explanation': parsed.get('plain_explanation', ''),
            'why_care': parsed.get('why_care', ''),
        }
    except (json.JSONDecodeError, KeyError):
        return summarize_item(item)


# === Batch curation (enhanced with scoring context) ===

CURATE_SYSTEM = """你是AI资讯日报的主编，负责对今日采集的新闻进行选题和排序。

选题原则：
1. 选取最有价值、最有影响力的25-40条新闻
2. 确保来源多样性，单一来源不超过5条
3. 确保分类均衡：今日头条3-5条，论文精选2-6条，开源热榜1-4条，其他栏目适量
4. 去除重复和低质量内容
5. 按重要性排序
6. AIbase关注度前5条必须入选（标记为aibase_required的条目）
7. 投融资、政策、大厂动态等热点优先

每条候选已包含一个初步评分(score)，越高越重要。请参考评分但不必完全依赖。
请输出JSON数组，每个元素包含：id(原列表序号), category(分类), priority(1-5, 5最重要)
不要包含未入选的条目。"""

CURATE_USER_TEMPLATE = """以下是今日采集的{total}条候选新闻（已按评分排序）：

{items_json}

请选题并排序，输出25-40条。确保AIbase的条目必选。"""


def curate_items(items: List[Dict]) -> List[Dict]:
    """Use LLM to curate and rank items."""
    simplified = []
    for i, item in enumerate(items):
        entry = {
            'id': i,
            'title': item.get('cn_title', item['title'])[:30],
            'source': item.get('source', ''),
            'score': round(item.get('_score', 0), 1),
        }
        if item.get('source', '') in ('AIbase 头条', 'AIbase', 'aibase'):
            entry['aibase_required'] = True
        simplified.append(entry)

    user_prompt = CURATE_USER_TEMPLATE.format(
        total=len(items),
        items_json=json.dumps(simplified, ensure_ascii=False, indent=2),
    )

    result = llm_chat(CURATE_SYSTEM, user_prompt, temperature=0.2)

    if not result:
        return items

    try:
        clean = re.sub(r'```json\s*', '', result)
        clean = re.sub(r'```\s*', '', clean).strip()
        curation = json.loads(clean)

        curated = []
        for entry in curation:
            idx = entry.get('id')
            if isinstance(idx, int) and 0 <= idx < len(items):
                item = items[idx].copy()
                item['category'] = entry.get('category', item.get('category', 'headline'))
                item['priority'] = entry.get('priority', 3)
                curated.append(item)

        return curated if curated else items
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"  [!] Curation parse error: {e}")
        return items
