"""HTML publisher — renders the daily report as WeChat-compatible HTML."""
import os
from datetime import datetime
from typing import List, Dict
from jinja2 import Environment, FileSystemLoader


SECTION_META = {
    'headline':    {'name': '今日头条',    'color': '#c0392b'},
    'investment':  {'name': '投融资',       'color': '#b8860b'},
    'policy':      {'name': '政策监管',     'color': '#2c5282'},
    'technology':  {'name': '技术突破',     'color': '#2e7d32'},
    'product':     {'name': '产品发布',     'color': '#6b46c1'},
    'bigtech':     {'name': '大厂动态',     'color': '#c05621'},
    'opinion':     {'name': '大V观点',     'color': '#c0392b'},
    'paper':       {'name': '论文精选',     'color': '#2c6e6b'},
    'opensource':  {'name': '开源热榜',     'color': '#b8336a'},
    'event':       {'name': '活动预告',     'color': '#2c5282'},
}

# opinion/event 已合并到 bigtech/product，不单独展示
SECTION_ORDER = ['headline', 'investment', 'policy', 'technology', 'product',
                 'bigtech', 'paper', 'opensource']

TOP_N = 5


def build_sections(articles: List[Dict]) -> List[Dict]:
    """Group articles by category into report sections."""
    sections = []
    for cat_key in SECTION_ORDER:
        cat_articles = [a for a in articles if a.get('category') == cat_key]
        meta = SECTION_META.get(cat_key, {'name': cat_key, 'color': '#666'})
        sections.append({
            'key': cat_key,
            'name': meta['name'],
            'color': meta['color'],
            'articles': cat_articles,
        })
    return sections


def build_top_items(articles: List[Dict]) -> List[Dict]:
    """Pick top N items for the quick-summary section.
    Prioritizes by signal grade then by score."""
    # Sort by signal grade priority then score
    grade_priority = {'重磅': 0, '值得关注': 1, '常规': 2}
    sorted_items = sorted(articles,
        key=lambda a: (
            grade_priority.get(a.get('signal_grade', '常规'), 2),
            -a.get('_score', 0)
        ))
    top = sorted_items[:TOP_N]
    for item in top:
        meta = SECTION_META.get(item.get('category', ''), {'color': '#666'})
        item['section_color'] = meta['color']
    return top


def build_dynamic_title(top_items: List[Dict], date_str: str) -> str:
    """Generate a keyword-rich title from top 2 items."""
    short_date = date_str[5:].replace('-', '月') + '日'
    if len(top_items) >= 2:
        t1 = top_items[0].get('cn_title', '')
        t2 = top_items[1].get('cn_title', '')
        # Truncate if too long
        if len(t1) + len(t2) > 24:
            t2 = t2[:10] + '…'
        return f"{t1}、{t2} | {short_date}AI日报"
    elif top_items:
        return f"{top_items[0].get('cn_title', '')} | {short_date}AI日报"
    return f"{date_str} AI资讯日报"


def generate_html(articles: List[Dict], date_str: str = None) -> str:
    """Render the daily report HTML."""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    sections = build_sections(articles)
    top_items = build_top_items(articles)
    dynamic_title = build_dynamic_title(top_items, date_str)
    total = len(articles)
    source_count = len(set(a.get('source', '') for a in articles))

    weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        weekday = weekdays[dt.weekday()]
        subtitle = f"每周一到周五准时送达 · {weekday}"
    except ValueError:
        subtitle = "每日精选 AI 行业资讯"

    template_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates')
    if not os.path.exists(template_dir):
        template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'templates')

    env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
    template = env.get_template('daily_report.html')

    html = template.render(
        date=date_str.replace('-', ''),
        date_str=date_str,
        dynamic_title=dynamic_title,
        subtitle=subtitle,
        total=total,
        source_count=source_count,
        sections=sections,
        top_items=top_items,
    )
    return html


def save_html(html: str, output_dir: str, date_str: str = None) -> str:
    """Save HTML to file and return the path."""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"ai-daily-{date_str}.html")
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  [HTML] Saved: {filepath}")
    return filepath
