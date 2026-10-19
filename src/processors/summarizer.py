"""LLM-powered summarization using GLM (ZhiPu) API.
Enhanced with multi-step prompt strategy learned from CloudFlare-AI-Insight-Daily."""
import os
import json
import re
import time
import random
from typing import Dict, List, Optional, Tuple
from openai import OpenAI
from utils.text import trim_title, truncate_text


_DEFAULT_BASE_URL = 'https://open.bigmodel.cn/api/paas/v4'
_DEFAULT_MODEL = 'glm-4.5'

# 探活选中的端点。None 表示还没探过，此时按主端点走（保持旧行为）。
_active_endpoint: Optional[Dict[str, str]] = None


def list_endpoints() -> List[Dict[str, str]]:
    """按优先级列出候选 LLM 端点。

    云端 CI 里 secrets.LLM_BASE_URL 可能指向公司内网地址，GitHub runner 根本到不了；
    而 llm_chat 会把连接异常吞成空串，整条流水线静默退化成"原文摘要兜底"。
    所以支持一组备用端点：LLM_FALLBACK_BASE_URL / LLM_FALLBACK_API_KEY / LLM_FALLBACK_MODEL。
    GitHub Actions 里备用端点直接用 GitHub Models（GITHUB_TOKEN 就够，不需要额外密钥）。
    """
    endpoints = [{
        'name': 'primary',
        'base_url': (os.environ.get('LLM_BASE_URL') or _DEFAULT_BASE_URL).strip(),
        'api_key': (os.environ.get('LLM_API_KEY') or '').strip(),
        'model': (os.environ.get('LLM_MODEL') or _DEFAULT_MODEL).strip(),
    }]
    fb_url = (os.environ.get('LLM_FALLBACK_BASE_URL') or '').strip()
    fb_key = (os.environ.get('LLM_FALLBACK_API_KEY') or '').strip()
    if fb_url and fb_key:
        endpoints.append({
            'name': 'fallback',
            'base_url': fb_url,
            'api_key': fb_key,
            'model': (os.environ.get('LLM_FALLBACK_MODEL') or _DEFAULT_MODEL).strip(),
        })
    return endpoints


def get_endpoint() -> Dict[str, str]:
    """当前生效的端点。探活成功后由 llm_preflight 设置。"""
    global _active_endpoint
    if _active_endpoint is None:
        _active_endpoint = list_endpoints()[0]
    return _active_endpoint


def get_llm_client(endpoint: Optional[Dict[str, str]] = None,
                   timeout: Optional[float] = None) -> OpenAI:
    """Create OpenAI-compatible client for GLM API."""
    ep = endpoint or get_endpoint()
    # timeout 必须显式设置：SDK 默认 600s，单条卡住会拖死整条流水线。
    # 探活走更短的 timeout，端点全挂时不用白等两轮。
    # max_retries 是 SDK 层重试，llm_chat 的指数退避是应用层重试，两者互补。
    return OpenAI(
        api_key=ep['api_key'],
        base_url=ep['base_url'],
        timeout=float(timeout if timeout is not None else os.environ.get('LLM_TIMEOUT', '120')),
        max_retries=int(os.environ.get('LLM_MAX_RETRIES', '1')),
    )


def get_model() -> str:
    return get_endpoint()['model']


def llm_chat(system_prompt: str, user_prompt: str, temperature: float = 0.3,
             attempts: Optional[int] = None) -> str:
    """Call LLM and return text response.

    单次抖动（429 限流、瞬时连接重置、返回空内容）以前会直接把这条打成
    "原文摘要兜底"，标题不中文化、标签为空。这里加指数退避重试，
    把偶发故障和端点彻底不可用区分开——后者由 llm_preflight 提前拦掉。
    """
    max_attempts = int(attempts or os.environ.get('LLM_CHAT_ATTEMPTS', '3'))
    client = get_llm_client()
    last_error = ''
    for attempt in range(1, max_attempts + 1):
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
            content = (resp.choices[0].message.content or '').strip()
            if content:
                return content
            last_error = '返回空内容'
        except Exception as e:
            last_error = f'{type(e).__name__}: {e}'
        if attempt < max_attempts:
            delay = min(2 ** attempt, 8) + random.uniform(0, 1)
            print(f"  [!] LLM 第 {attempt}/{max_attempts} 次失败（{last_error}），"
                  f"{delay:.1f}s 后重试")
            time.sleep(delay)
    print(f"  [!] LLM call failed after {max_attempts} attempts: {last_error}")
    return ""


def _probe(endpoint: Dict[str, str], timeout: float = 20.0) -> Tuple[bool, str]:
    """对单个端点发一次最小请求，确认真的能出字。"""
    label = f"{endpoint['base_url']} / {endpoint['model']}"
    if not endpoint['api_key']:
        return False, f"{label}：API key 为空"
    try:
        resp = get_llm_client(endpoint, timeout=timeout).chat.completions.create(
            model=endpoint['model'],
            messages=[{"role": "user", "content": "ping"}],
            temperature=0,
            max_tokens=8,
        )
        if not (resp.choices[0].message.content or '').strip():
            return False, f"{label}：连通但返回空内容"
        return True, f"{label}：可用"
    except Exception as e:
        return False, f"{label}：不可达（{type(e).__name__}: {e}）"


def llm_preflight(timeout: float = 20.0) -> Tuple[bool, str]:
    """按优先级探活所有候选端点，选中第一个可用的。

    返回 (是否可用, 详情)。详情里会写明用的是主端点还是备用端点；
    全挂时把每个端点的失败原因都列出来，方便一眼定位是密钥问题还是网络问题。
    """
    global _active_endpoint
    candidates = list_endpoints()
    details = []
    for ep in candidates:
        ok, detail = _probe(ep, timeout=timeout)
        tag = '' if ep['name'] == 'primary' else '（备用端点）'
        details.append(f"[{ep['name']}]{tag} {detail}")
        if ok:
            _active_endpoint = ep
            return True, '；'.join(details)
    _active_endpoint = candidates[0]
    return False, '；'.join(details)


# === Enhanced System Prompts (learned from CloudFlare project) ===

SUMMARIZE_SYSTEM = """你是一名资深AI科技记者，在科技媒体干了十年，刚跳出来自己做公众号"AI洞察局"。
你不是AI，你是人。你写的东西要像人写的。

你的写作原则：
1. **忠于原文**：摘要必须严格来源于原文，不捏造、不歪曲、不添加原文没有的信息。
2. **说人话**：像跟同事聊天一样写，不要用书面套话。把"此举旨在"换成"目的是"，把"被视为"换成"可以看作"，但更好的方式是直接说出来。
3. **信息密度高**：90-150字塞进最多的有效信息——谁做了什么、多少钱、什么效果、什么影响。少用形容词，多用事实。
4. **句式要活**：不要每条都"XX公司发布XX，具有XX特点，将用于XX领域"这个套路。可以倒装、可以短句、可以设问、可以一句话开头。
5. **技术要准**：公司名、产品名、技术术语用中文规范名，不确定的保留英文原文。

"忠于原文"的硬约束（违反任何一条即不合格，这是最高优先级）：
- **禁止加戏**：不得添加原文没有出现的背景、动机、目的、因果、评价、趋势判断或结语。
  典型错误：原文只说"关闭 RSS"，摘要写成"为防 AI 爬虫抓取、面对 AI 公司对训练数据的渴求"；
  原文只说"支付罚金风险"，摘要补充"罚款 1 万新币"；原文没说"仍在调查中"，摘要加"目前争议仍在调查中"。
- **限定词一个都不能丢**：原文的"最高""约""部分""可能""据称""仅…之一"必须原样保留。
  典型错误：把"up to 2x faster"写成"快两倍"；把"部分靠电"写成"靠电"；把"大约4个月"写成"仅4个月"。
- **禁止因果倒置与程度改写**：原文说"增长平稳可预期"，不能写成"增长乏力"。
- **实体必须出自原文**：公司名、产品名、模型名、金额、日期，原文没出现的就一个字都不要写。
- **不下结论**：结尾不要自行升华（如"XX生态的拼图又补了一块""任务范围又扩了一圈"）。
  写不下的信息宁可不写，也不要自己补。

绝对禁止出现的AI腔（出现任何一个就重写）：
- "此举旨在" "标志着" "被视为" "引发业界" "值得关注的是" "具有重要意义"
- "据悉" "值得一提的是" "不仅…而且" "一方面…另一方面"
- 以"近日，"开头
- 以"该产品/该技术/该公司"开头的句式
- 任何"赋能""助力""打造""引领"等公关稿高频词

标题要求（最重要）：
- 必须是**完整、自洽的中文短句**，20-28字
- **严禁把原标题机械截断**——宁可重新组织成一个更短的完句，也绝不能留下半截话
- 截断的标题一律视为不合格，例如"作家工会诉OpenAI侵权案解封新"、"围观AI智"、"这种产"这种
  以半个词收尾的都是错的
- 正确做法：删掉次要信息，保留主谓宾完整的短句，如"作家工会诉OpenAI案关键文件解封"
- 不用"震惊""重磅""突破"等夸张词
- 英文标题中文化，但保留品牌名英文（如GPT、Claude）
- 好标题例子："OpenAI开放GPT-5 API，开发者免费调用3个月"
- 差标题例子："OpenAI又有大动作！GPT-5重磅来袭"、"OpenAI开放GPT-5 API，开发者免"

任务：
1. 将标题改写为完整的中文短标题（必须是完整短句，绝不截断成半句话），控制在20-28字
2. 将摘要改写为90-150字的中文摘要
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
  "cn_title": "完整通顺的中文标题，20-28字，必须是完整短句、不得截断成半句话",
  "cn_summary": "中文摘要，90-150字，信息密度高，包含关键信息",
  "tags": ["标签1", "标签2", "标签3"],
  "category": "分类key"
}}"""


def parse_llm_json(result: str) -> Optional[Dict]:
    """Parse a JSON object out of an LLM reply.

    模型经常在 JSON 外包 ```json 围栏、前后加说明文字，甚至输出被截断。
    这里先剥围栏，再抓第一个完整的 {...} 块，最后才放弃。
    """
    if not result:
        return None
    clean = re.sub(r'```json\s*', '', result)
    clean = re.sub(r'```\s*', '', clean).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    # 退一步：抓取第一个 JSON 对象（模型常在前后加多余文字）
    depth, start = 0, -1
    for i, ch in enumerate(clean):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(clean[start:i + 1])
                except json.JSONDecodeError:
                    start = -1  # 这一段不行，继续找下一个
    return None


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
            'cn_title': trim_title(item['title']),
            'cn_summary': truncate_text(item.get('summary', ''), 160),
            'tags': [],
            'category': item.get('category_hint', '行业动态'),
            'llm_fallback': True,
        }

    parsed = parse_llm_json(result)
    if parsed is None:
        # 解析失败就重试一次；绝不把模型原始输出（含 ```json 围栏）当成摘要写进日报
        print(f"  [!] Failed to parse LLM response for '{item['title'][:30]}...', retrying")
        result = llm_chat(SUMMARIZE_SYSTEM, user_prompt, temperature=0.2)
        parsed = parse_llm_json(result)

    if parsed is None:
        print(f"  [x] Give up on '{item['title'][:30]}...', fall back to source summary")
        return {
            'cn_title': trim_title(item['title']),
            'cn_summary': truncate_text(item.get('summary', ''), 160),
            'tags': [],
            'category': item.get('category_hint', '行业动态'),
            'llm_fallback': True,
        }

    return {
        'cn_title': trim_title(parsed.get('cn_title', item['title'])),
        'cn_summary': truncate_text(parsed.get('cn_summary', ''), 160),
        'tags': parsed.get('tags', [])[:3],
        'category': parsed.get('category', 'headline'),
        'llm_fallback': False,
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
  "cn_title": "论文中文标题，20-28字，必须是完整短句、不得截断",
  "one_liner": "一句话总结，30字以内，用通俗语言",
  "key_points": ["核心要点1（20字以内）", "核心要点2", "核心要点3"],
  "plain_explanation": "通俗解读，100-150字，让非专业读者也能理解。用类比和日常语言。",
  "why_care": "为什么值得关注，50-80字。说明对现实世界的影响。",
  "tags": ["标签1", "标签2"],
  "cn_summary": "综合摘要，90-150字，包含论文核心贡献和创新点",
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

    parsed = parse_llm_json(result)
    if parsed is not None:
        return {
            'cn_title': trim_title(parsed.get('cn_title', item['title'])),
            'cn_summary': truncate_text(parsed.get('cn_summary', ''), 160),
            'tags': parsed.get('tags', [])[:3],
            'category': 'paper',
            'one_liner': parsed.get('one_liner', ''),
            'key_points': parsed.get('key_points', []),
            'plain_explanation': parsed.get('plain_explanation', ''),
            'why_care': parsed.get('why_care', ''),
            'llm_fallback': False,
        }
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
