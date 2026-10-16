"""正文补全 — 为 RSS 摘要过短的条目抓取原文正文。

为什么需要这一步：
不少优质源（实测 InfoQ 摘要中位数 7 字、量子位 17 字、Hacker News 28 字）
在 RSS 里只给一句话甚至只有"点击查看原文>"。这种空壳原文送进 LLM 摘要时，
模型只能凭标题脑补细节，一致性自检几乎必然判 fail（加戏/无中生有）。
实测 2026-09-21：35 条里 17 条 fail，其中 fail 组摘要中位数只有 13 字，
而通过组最小 74 字 —— 分界非常清晰。

所以与其事后过滤，不如在摘要之前把正文抓回来。
抓不到正文的（反爬/超时）才丢弃，因为它们确实没有可核实的原文。
"""
import concurrent.futures
from typing import Dict, List, Tuple

MIN_CHARS = 50        # 低于这个长度视为"原文不足"
DEFAULT_TIMEOUT = 20  # 单页抓取超时（秒）
MAX_CHARS = 2500      # 正文截断长度，够摘要用即可


def fetch_article_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """用 trafilatura 抓取正文，失败返回空串。"""
    if not url or not url.startswith('http'):
        return ''
    try:
        import trafilatura
        # 用 trafilatura 自己的下载器：内部已处理编码与常见反爬
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ''
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_precision=False,
        )
        return (text or '').strip()
    except Exception:
        # 抓取失败不该中断整条流水线
        return ''


def enrich_source_text(
    items: List[Dict],
    enabled: bool = True,
    min_chars: int = MIN_CHARS,
    max_workers: int = 8,
    timeout: int = DEFAULT_TIMEOUT,
    max_chars: int = MAX_CHARS,
) -> Tuple[List[Dict], Dict]:
    """把摘要过短的条目用正文补全；补不全的剔除。

    返回 (保留的条目, 统计信息)。
    """
    stats = {'total': len(items), 'thin': 0, 'enriched': 0, 'dropped': 0}

    if not enabled:
        return items, stats

    thin = [
        i for i in items
        if len(i.get('summary', '') or '') < min_chars
        and (i.get('url', '') or '').startswith('http')
    ]
    stats['thin'] = len(thin)

    if thin:
        fetched: Dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(fetch_article_text, i['url'], timeout): i['url'] for i in thin}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    text = fut.result()
                except Exception:
                    text = ''
                if text:
                    fetched[futures[fut]] = text

        kept: List[Dict] = []
        for item in items:
            summary = item.get('summary', '') or ''
            if len(summary) >= min_chars:
                kept.append(item)
                continue
            body = fetched.get(item.get('url', ''), '')
            if len(body) >= min_chars:
                # 直接覆盖 summary：下游 summarizer / coherence_check 都以 summary 作为原文
                item['summary'] = body[:max_chars]
                stats['enriched'] += 1
                kept.append(item)
            else:
                stats['dropped'] += 1
        items = kept

    stats['kept'] = len(items)
    return items, stats
