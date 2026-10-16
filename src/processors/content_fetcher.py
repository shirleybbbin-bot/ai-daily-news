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
STAGE_TIMEOUT = 300   # 整个补全阶段的兜底墙钟（秒），防止个别站点拖死流水线
DEFAULT_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)


def fetch_article_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """抓取原文正文，失败返回空串。

    网络请求交给 requests 并显式设置 (连接, 读取) 双超时；trafilatura 只负责解析。
    不再调用 trafilatura.fetch_url —— 它不接受外部传入的 timeout，实测会在个别站点上
    永久阻塞工作线程，而 ThreadPoolExecutor 的 with 块要等所有 future 完成，
    结果是单个卡死的 URL 把整个正文补全阶段无限期挂住。
    """
    if not url or not url.startswith('http'):
        return ''
    try:
        import requests
        resp = requests.get(
            url,
            timeout=(min(8, timeout), timeout),
            headers={
                'User-Agent': DEFAULT_UA,
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            },
            allow_redirects=True,
        )
        if resp.status_code != 200 or not resp.text:
            return ''
        import trafilatura
        text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
            favor_precision=False,
            url=url,
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
    stage_timeout: int = STAGE_TIMEOUT,
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
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        try:
            futures = {ex.submit(fetch_article_text, i['url'], timeout): i['url'] for i in thin}
            done, not_done = concurrent.futures.wait(futures, timeout=stage_timeout)
            for fut in done:
                try:
                    text = fut.result()
                except Exception:
                    text = ''
                if text:
                    fetched[futures[fut]] = text
            if not_done:
                stats['stage_timeout'] = len(not_done)
                for fut in not_done:
                    fut.cancel()
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

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
