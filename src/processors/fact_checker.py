"""Fact checker — 多重校对：事件真实性 + 时效性 + 准确性。

三重校验：
1. 真实性：源URL可达 + 关键实体（公司名/产品名）在原文中出现
2. 时效性：发布日期在 lookback 窗口内（默认48小时）
3. 准确性：摘要中的关键数字/金额在原文中可溯源
"""
import re
import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from urllib.parse import urlparse

import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

# 从摘要中提取关键数字的模式
NUMBER_PATTERNS = [
    re.compile(r'(\d+(?:\.\d+)?)\s*(?:亿|万|百万|千万|亿万美元|亿美元|元)', re.I),
    re.compile(r'(\d+(?:\.\d+)?)\s*%', re.I),
    re.compile(r'(\d{4})\s*年', re.I),
    re.compile(r'\$(\d+(?:\.\d+)?)\s*(?:billion|million|B|M)', re.I),
]

# 发布日期提取模式
DATE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']pubdate["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']date["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I),
    re.compile(r'"datePublished"[:\s]*"([^"]+)"', re.I),
]

# 从标题/摘要中提取关键实体（公司名、产品名）
ENTITY_HINTS = [
    'OpenAI', 'GPT', 'Google', 'Gemini', 'Meta', 'Apple', 'Microsoft', '百度', '阿里',
    '腾讯', '字节', '华为', '英伟达', 'Nvidia', 'Claude', 'Anthropic', 'xAI', 'Grok',
    '智谱', 'GLM', '通义', 'Qwen', 'Stability', 'Hugging', 'Perplexity', 'Mistral',
    'Moonshot', '月之暗面', 'DeepSeek', 'Cognition', 'Devin', 'Uber', 'Wayve',
]


def check_url_accessible(url: str, timeout: int = 10) -> Tuple[bool, str]:
    """校验真实性：源URL是否可达。"""
    if not url or not url.startswith('http'):
        return False, 'URL无效'
    try:
        resp = requests.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return True, 'OK'
        if resp.status_code in (403, 405):
            # 有些站点禁止HEAD，尝试GET
            resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            if resp.status_code == 200:
                return True, 'OK(GET)'
            return False, f'HTTP {resp.status_code}'
        return False, f'HTTP {resp.status_code}'
    except requests.exceptions.Timeout:
        return False, '超时'
    except Exception as e:
        return False, str(e)[:30]


def extract_publish_date(html: str) -> datetime | None:
    """从HTML中提取发布日期。"""
    for pattern in DATE_PATTERNS:
        match = pattern.search(html)
        if match:
            date_str = match.group(1)
            # 尝试多种格式解析
            for fmt in [
                '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d',
            ]:
                try:
                    return datetime.strptime(date_str[:25], fmt)
                except ValueError:
                    continue
    return None


def check_timeliness(item: Dict, html: str, lookback_hours: int = 48) -> Tuple[bool, str]:
    """校验时效性：发布日期是否在 lookback 窗口内。"""
    pub_date = extract_publish_date(html)
    if not pub_date:
        # 如果提取不到日期，检查RSS pubDate
        rss_date = item.get('published', '')
        if rss_date:
            try:
                pub_date = datetime.strptime(rss_date[:25], '%a, %d %b %Y %H:%M:%S')
            except ValueError:
                pass

    if not pub_date:
        return True, '日期未知(跳过)'

    now = datetime.now(pub_date.tzinfo) if pub_date.tzinfo else datetime.now()
    diff = now - pub_date
    hours_ago = diff.total_seconds() / 3600

    if hours_ago > lookback_hours:
        return False, f'发布于{hours_ago:.0f}小时前(超{lookback_hours}h)'
    if hours_ago < -2:
        return False, f'发布日期异常(未来时间, {hours_ago:.0f}h)'
    return True, f'{hours_ago:.0f}h前'


def extract_key_numbers(text: str) -> List[str]:
    """从文本中提取关键数字（金额、百分比等）。"""
    numbers = []
    for pattern in NUMBER_PATTERNS:
        for match in pattern.finditer(text):
            numbers.append(match.group(0).strip())
    return numbers


def check_accuracy(item: Dict, html: str) -> Tuple[bool, List[str]]:
    """校验准确性：摘要中的关键数字是否在原文中出现。"""
    summary = item.get('cn_summary', '') + item.get('cn_title', '')
    key_numbers = extract_key_numbers(summary)

    if not key_numbers:
        return True, ['无关键数字(跳过)']

    # 取原文前80KB做匹配
    source_text = html[:80000]
    # 去HTML标签
    source_text = re.sub(r'<[^>]+>', ' ', source_text)
    source_text = re.sub(r'\s+', ' ', source_text)

    missing = []
    for num in key_numbers:
        # 提取数字核心部分进行匹配（去掉单位）
        core_num = re.search(r'\d+(?:\.\d+)?', num)
        if core_num and core_num.group() in source_text:
            continue
        else:
            missing.append(num)

    if missing:
        return False, missing
    return True, key_numbers


def check_entity_mentioned(item: Dict, html: str) -> Tuple[bool, List[str]]:
    """校验真实性：标题中的关键实体在原文HTML中出现。"""
    title = item.get('cn_title', '') + item.get('title', '')
    found_entities = []
    missing_entities = []

    source_text = html[:80000].lower()
    for entity in ENTITY_HINTS:
        if entity.lower() in title.lower():
            if entity.lower() in source_text:
                found_entities.append(entity)
            else:
                missing_entities.append(entity)

    if missing_entities:
        return False, missing_entities
    return True, found_entities


def fact_check_item(item: Dict, fetch_content: bool = True) -> Dict:
    """对单条新闻执行三重校验。返回带 fact_check 结果的 item。"""
    url = item.get('url', '')
    result = {
        'url_accessible': False,
        'url_status': '',
        'timeliness': False,
        'timeliness_detail': '',
        'accuracy': False,
        'accuracy_detail': [],
        'entity_match': False,
        'entity_detail': [],
        'passed': False,
        'issues': [],
    }

    if not url or not url.startswith('http'):
        result['issues'].append('无有效URL')
        item['fact_check'] = result
        return item

    # 1. URL可达性
    accessible, status = check_url_accessible(url)
    result['url_accessible'] = accessible
    result['url_status'] = status
    if not accessible:
        result['issues'].append(f'源URL不可达: {status}')

    # 如果需要抓取原文内容做深度校验
    html = ''
    if fetch_content and accessible:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
            if resp.status_code == 200:
                html = resp.text[:100000]
        except Exception:
            pass

    if html:
        # 2. 时效性
        timely, detail = check_timeliness(item, html)
        result['timeliness'] = timely
        result['timeliness_detail'] = detail
        if not timely:
            result['issues'].append(f'时效性问题: {detail}')

        # 3. 准确性 - 数字校验
        accurate, numbers = check_accuracy(item, html)
        result['accuracy'] = accurate
        result['accuracy_detail'] = numbers
        if not accurate:
            result['issues'].append(f'数字无法溯源: {numbers}')

        # 4. 真实性 - 实体匹配
        entity_ok, entities = check_entity_mentioned(item, html)
        result['entity_match'] = entity_ok
        result['entity_detail'] = entities
        if not entity_ok:
            result['issues'].append(f'实体未在原文出现: {entities}')

    # 综合判定
    result['passed'] = (
        result['url_accessible']
        and result['timeliness']
        and result['accuracy']
        and result['entity_match']
    )
    # 如果只做了URL校验（没抓到内容），放宽判定
    if not html and result['url_accessible']:
        result['passed'] = True
        result['issues'].append('仅URL校验通过(未抓取原文)')

    item['fact_check'] = result
    return item


def fact_check_batch(items: List[Dict], lookback_hours: int = 48) -> Tuple[List[Dict], Dict]:
    """对全部新闻执行多重校对。返回带结果的 items + 统计。"""
    print("\n🔎 === 多重事实校对 ===")
    print(f"  校验维度: 真实性(URL+实体) | 时效性({lookback_hours}h) | 准确性(数字溯源)")

    checked = []
    stats = {
        'total': len(items),
        'passed': 0,
        'flagged': 0,
        'failed': 0,
        'issues': [],
    }

    for i, item in enumerate(items):
        title = item.get('cn_title', '')[:25]
        try:
            result = fact_check_item(item)
            fc = result.get('fact_check', {})
            passed = fc.get('passed', False)
            issues = fc.get('issues', [])

            if passed and not issues:
                stats['passed'] += 1
                status_icon = '✅'
            elif passed and issues:
                stats['passed'] += 1
                status_icon = '⚠️'
            else:
                stats['flagged'] += 1
                status_icon = '❌'
                for issue in issues:
                    stats['issues'].append({
                        'title': title,
                        'issue': issue,
                    })

            print(f"  [{status_icon}] ({i+1}/{len(items)}) {title}...")
            if issues:
                for issue in issues:
                    print(f"       → {issue}")

            checked.append(result)
        except Exception as e:
            print(f"  [💥] ({i+1}/{len(items)}) {title}... → {e}")
            checked.append(item)
            stats['failed'] += 1

    print(f"\n  📊 校对结果: ✅{stats['passed']} ⚠️{stats['flagged']} 💥{stats['failed']}")
    if stats['issues']:
        print(f"  🚩 待人工审核 ({len(stats['issues'])}项):")
        for issue in stats['issues'][:10]:
            print(f"    • {issue['title']}... → {issue['issue']}")

    return checked, stats


if __name__ == '__main__':
    import sys
    data_file = sys.argv[1] if len(sys.argv) > 1 else 'today_data.json'
    with open(data_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    checked, stats = fact_check_batch(data)
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 事实校对完成，结果已写入 {data_file}")
    print(f"   通过: {stats['passed']}/{stats['total']}")
    print(f"   标记: {stats['flagged']}/{stats['total']}")
