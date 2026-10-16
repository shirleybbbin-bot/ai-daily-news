"""Text processing utilities."""
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from typing import Optional


def clean_text(text: str) -> str:
    """Clean up text: remove extra whitespace, HTML entities, etc."""
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return text


def truncate_text(text: str, max_chars: int = 160) -> str:
    """Truncate text to max_chars, ending at a sentence boundary if possible."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    for sep in ['。', '！', '？', '；', '. ', '!', '?', '；']:
        idx = truncated.rfind(sep)
        if idx > max_chars * 0.6:
            return truncated[:idx + 1]
    # 省略号也要占一个字符，否则会超出 max_chars
    return truncated.rstrip()[:max_chars - 1] + '…'


# 标题最大字数。宁可长一点，也不要把"性能逼近Astra且"这种半句话切出来。
TITLE_MAX_CHARS = 40
# 允许断句的位置（按优先级尝试，切完必须仍是完整语义片段）
_TITLE_SEPARATORS = ['。', '，', '、', '：', '；', '—', '-', ' ']


def trim_title(title: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    """Trim a title to max_chars WITHOUT leaving a dangling half-clause.

    直接用 [:30] 硬切会把标题切成 "...性能逼近Astra且" 这种残句。
    这里改为：先按自然断点回退，找不到断点才退化到省略号。
    """
    if not title:
        return ""
    title = title.strip()
    if len(title) <= max_chars:
        return title
    window = title[:max_chars]
    for sep in _TITLE_SEPARATORS:
        idx = window.rfind(sep)
        # 断点不能太靠前，否则信息量损失过大
        if idx >= max_chars * 0.5:
            return window[:idx].rstrip('，、：；—- ')
    return window.rstrip()[:max_chars - 1] + '…'


def is_english_title(title: str) -> bool:
    """Check if a title is primarily English."""
    if not title:
        return False
    english_chars = sum(1 for c in title if ord(c) < 128 and c.isalpha())
    total_alpha = sum(1 for c in title if c.isalpha())
    if total_alpha == 0:
        return False
    return english_chars / total_alpha > 0.6


def similarity(a: str, b: str) -> float:
    """Calculate text similarity ratio between two strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def extract_domain(url: str) -> str:
    """Extract domain name from URL."""
    if not url:
        return ""
    clean = url.replace('https://', '').replace('http://', '')
    domain = clean.split('/')[0]
    # Remove www. prefix
    if domain.startswith('www.'):
        domain = domain[4:]
    return domain


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse various date formats from RSS feeds."""
    formats = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%SZ',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def is_recent(date_str: str, hours: int = 24) -> bool:
    """Check if a date string is within the last N hours."""
    dt = parse_date(date_str)
    if not dt:
        return True  # If we can't parse, include it
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - dt).total_seconds() <= hours * 3600


def keyword_match(text: str, keywords: str) -> bool:
    """Check if text contains any of the comma-separated keywords (case-insensitive)."""
    if not keywords or not text:
        return True  # No filter = match all
    text_lower = text.lower()
    for kw in keywords.split(','):
        kw = kw.strip().lower()
        if kw and kw in text_lower:
            return True
    return False
