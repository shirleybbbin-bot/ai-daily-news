"""Quality validation and calibration mechanism."""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

# 反 AI 腔黑名单。calibrator / summarizer 的提示词里已经要求避开这些词，
# 这里做事后硬校验：提示词漏网时也能在质检阶段被拦住，而不是直接发出去。
CLICHE_WORDS = [
    '此举旨在', '标志着', '被视为', '引发业界', '值得关注的是', '据悉',
    '值得一提的是', '具有重要意义', '赋能', '助力', '打造', '引领', '重塑',
]

# "该X"式书面指代是最容易被一眼认出的机器味，按中文量词习惯逐词替换
AI_TRACE_MAP = {
    '该模型': '这款模型', '该功能': '这一功能', '该产品': '这款产品',
    '该技术': '这项技术', '该公司': '这家公司', '该方案': '这套方案',
    '该服务': '这项服务', '该系统': '这套系统', '该工具': '这款工具',
    '该研究': '这项研究', '该报告': '这份报告', '该数据': '这些数据',
    '该论文': '这篇论文', '该芯片': '这款芯片', '该设备': '这台设备',
}
AI_TRACE_RE = re.compile('|'.join(re.escape(k) for k in AI_TRACE_MAP))

AIBASE_KEY = 'aibase'

# 需要检查反 AI 腔的中文字段
_CN_FIELDS = ('cn_title', 'cn_summary', 'editor_note')


def _import(module: str, package: str):
    """src/ 内部用裸 import，外部调用时用包路径，两种方式都要能跑。"""
    try:
        return __import__(module, fromlist=['*'])
    except ImportError:
        return __import__(f'src.{package}', fromlist=['*'])


def normalize_cat(item: Dict) -> str:
    """复用发布器的分类归一化，避免质检和渲染各算一套分类。"""
    mod = _import('publishers.html_publisher', 'publishers.html_publisher')
    return mod.normalize_category(item)


def is_junk(item: Dict) -> bool:
    """复用去重阶段的登录页／跳转页判定。"""
    try:
        mod = _import('processors.dedup', 'processors.dedup')
        return bool(mod.is_junk_item(item))
    except Exception:
        return False


def fix_ai_trace(text: str) -> str:
    """把"该模型/该功能"这类书面指代改成带量词的口语说法。"""
    if not text:
        return text
    return AI_TRACE_RE.sub(lambda m: AI_TRACE_MAP[m.group(0)], text)


def find_cliches(items: List[Dict]) -> List[Tuple[int, str, str, str]]:
    """返回 (序号, 字段, 命中词, 上下文)。"""
    hits = []
    seen = set()
    for i, item in enumerate(items):
        for field in _CN_FIELDS:
            text = item.get(field) or ''
            for word in CLICHE_WORDS:
                if word in text and (i, word) not in seen:
                    seen.add((i, word))
                    pos = text.find(word)
                    hits.append((i, field, word, text[max(0, pos - 8):pos + len(word) + 8]))
    return hits


def _parse_pub_date(value) -> datetime:
    """解析 pub_date。格式很杂（ISO 带冒号偏移、+0000、RFC-2822 GMT…），
    交给采集阶段同一个 parse_date，避免质检和采集对同一条数据得出不同结论。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    parsed = None
    try:
        parsed = _import('utils.text', 'utils.text').parse_date(text)
    except Exception:
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            parsed = None
    if parsed is None:
        # 采集器偶尔给出 "2026-10-19 01:00:00 GMT" 这种混合格式，规整后再试一次
        normalized = re.sub(r'\s+(GMT|UTC)$', '+00:00', text)
        if 'T' not in normalized and ' ' in normalized:
            normalized = normalized.replace(' ', 'T', 1)
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def freshness_stats(items: List[Dict], max_age_hours: float,
                    now: datetime = None) -> Dict:
    """统计有多少条落在时效窗口内。窗口外的说明可能是昨天的旧稿。"""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    fresh, stale, unknown = 0, 0, 0
    newest = None
    for item in items:
        dt = _parse_pub_date(item.get('pub_date'))
        if dt is None:
            unknown += 1
            continue
        newest = dt if newest is None or dt > newest else newest
        if dt >= cutoff:
            fresh += 1
        else:
            stale += 1
    return {
        'fresh': fresh, 'stale': stale, 'unknown': unknown,
        'total': len(items), 'max_age_hours': max_age_hours,
        'ratio': round(fresh / len(items), 3) if items else 0.0,
        'newest': newest.isoformat() if newest else '',
    }


def top_items(items: List[Dict], top_n: int = 5) -> List[Dict]:
    """与 calibrator._top_item_key 一致的排序：信号分级优先，再按分数。"""
    grade_priority = {'重磅': 0, '值得关注': 1, '常规': 2}
    return sorted(items, key=lambda a: (grade_priority.get(a.get('signal_grade', '常规'), 2),
                                       -float(a.get('_score', 0) or 0)))[:top_n]


def validate_report(items: List[Dict], settings: Dict) -> Tuple[bool, List[str]]:
    """Run all quality checks. Returns (passed, issues)."""
    issues = []
    qc = settings.get('quality_control', {})

    # Check 1: Item count
    min_items = settings.get('pipeline', {}).get('min_items', 25)
    max_items = settings.get('pipeline', {}).get('max_items', 40)
    if len(items) < min_items:
        issues.append(f"❌ 条目数量不足：{len(items)} < {min_items}条")
    if len(items) > max_items:
        issues.append(f"⚠️ 条目数量过多：{len(items)} > {max_items}条")

    # Check 2: Summary length
    if qc.get('check_summary_length', True):
        min_chars = settings.get('pipeline', {}).get('summary_min_chars', 90)
        max_chars = settings.get('pipeline', {}).get('summary_max_chars', 160)
        for i, item in enumerate(items):
            summary = item.get('cn_summary', '')
            if len(summary) < min_chars:
                issues.append(f"❌ 摘要过短 (#{i}): '{item.get('cn_title', '')[:20]}' → {len(summary)}字 (需{min_chars}-{max_chars}字)")
            if len(summary) > max_chars:
                issues.append(f"⚠️ 摘要过长 (#{i}): '{item.get('cn_title', '')[:20]}' → {len(summary)}字 (需{min_chars}-{max_chars}字)")

    # Check 3: No duplicate titles
    if qc.get('check_no_duplicates', True):
        seen = set()
        for i, item in enumerate(items):
            title = item.get('cn_title', item.get('title', ''))
            key = title.lower().strip()
            if key in seen:
                issues.append(f"❌ 重复标题: '{title[:30]}'")
            seen.add(key)
        seen_urls = set()
        for item in items:
            url = (item.get('url') or '').strip()
            if url and url in seen_urls:
                issues.append(f"❌ 重复链接: {url[:60]}")
            seen_urls.add(url)

    # Check 4: English titles must be translated
    if qc.get('check_no_english_titles', True):
        for i, item in enumerate(items):
            title = item.get('cn_title', '')
            letters = [c for c in title if c.isalpha()]
            if letters and all(ord(c) < 128 for c in letters):
                issues.append(f"❌ 未中文化标题 (#{i}): '{title}'")

    # Check 5: Tags count
    if qc.get('check_tags_count', True):
        min_tags = settings.get('pipeline', {}).get('tags_per_item', 3) - 1
        for i, item in enumerate(items):
            tags = item.get('tags', [])
            if len(tags) < min_tags:
                issues.append(f"⚠️ 标签不足 (#{i}): '{item.get('cn_title', '')[:20]}' → {len(tags)}个标签")

    # Check 6: Source diversity
    if qc.get('require_source_diversity', True):
        min_sources = qc.get('min_sources', 4)
        sources = set(item.get('source', '') for item in items)
        if len(sources) < min_sources:
            issues.append(f"❌ 来源单一：仅{len(sources)}个来源 (需≥{min_sources})")
        # Check max per source
        max_per_source = qc.get('max_items_per_source', 5)
        source_counts = {}
        for item in items:
            s = item.get('source', '')
            source_counts[s] = source_counts.get(s, 0) + 1
        for s, count in source_counts.items():
            if count > max_per_source:
                issues.append(f"⚠️ 单一来源过多：'{s}' 有 {count}条 (上限{max_per_source})")

    # Check 7: Category coverage（用发布器同一套归一化，否则中文分类名会被误判为缺失）
    cats = {}
    for item in items:
        key = normalize_cat(item)
        cats[key] = cats.get(key, 0) + 1
    required_cats = set(qc.get('required_categories', ['headline', 'paper']))
    missing = required_cats - set(cats)
    if missing:
        issues.append(f"⚠️ 缺少必要分类：{sorted(missing)}")
    unknown_cats = set(cats) - set(required_cats) - {
        'investment', 'policy', 'technology', 'product', 'bigtech',
        'paper', 'opensource', 'opinion', 'event', 'headline'}
    if unknown_cats:
        issues.append(f"⚠️ 分类无法归一化（会落入「其他动态」）：{sorted(unknown_cats)}")

    # Check 8: Image coverage
    min_image_ratio = qc.get('min_image_ratio', 0.5)
    items_with_images = sum(1 for i in items if i.get('image_url', ''))
    image_ratio = items_with_images / len(items) if items else 0
    if image_ratio < min_image_ratio:
        issues.append(f"⚠️ 配图率偏低：{items_with_images}/{len(items)} ({image_ratio:.0%})，建议≥{min_image_ratio:.0%}")

    # Check 9: Freshness —— 拦住"拿昨天的数据当今天日报"这种静默失败
    max_age = qc.get('max_item_age_hours',
                     settings.get('pipeline', {}).get('lookback_hours', 24) * 1.5)
    fresh = freshness_stats(items, max_age)
    dated = fresh['fresh'] + fresh['stale']
    if items and dated and fresh['fresh'] == 0:
        # 有日期但全都过期，基本可以断定是拿旧数据顶替今天的稿
        issues.append(f"❌ 时效性失败：{dated} 条有日期的条目没有一条落在 {max_age:.0f} 小时内，"
                      f"最新一条是 {fresh['newest'][:16]}，可能是旧数据")
    elif items and not dated:
        # 全部条目都没有可解析日期（采集器没给），只能提示，不能据此判死
        issues.append(f"⚠️ 无法判断时效性：{fresh['unknown']} 条 pub_date 为空或格式不识别")
    elif items and fresh['ratio'] < qc.get('min_fresh_ratio', 0.6):
        issues.append(f"⚠️ 时效性偏低：{fresh['fresh']}/{fresh['total']} 条在 {max_age:.0f} 小时内"
                      f"（过期 {fresh['stale']}，无日期 {fresh['unknown']}）")

    # Check 10: AIbase 关注度前5（settings 里一直有这项，之前没人读）
    if qc.get('require_aibase_top5', True):
        aibase_n = sum(1 for i in items
                       if AIBASE_KEY in (i.get('source_domain') or '').lower()
                       or AIBASE_KEY in (i.get('source') or '').lower())
        need = qc.get('min_aibase_items', 5)
        if aibase_n == 0:
            issues.append(f"❌ 缺 AIbase 条目：0 条（需≥{need}，AIbase 关注度前5 是硬要求）")
        elif aibase_n < need:
            issues.append(f"⚠️ AIbase 条目偏少：{aibase_n} 条（需≥{need}）")

    # Check 11: 前 N 条要有编辑判断（模板靠 editor_note 渲染"洞察"块）
    top_n = (settings.get('editor_note', {}) or {}).get('top_n', 5)
    if qc.get('check_editor_notes', True):
        missing_notes = [it.get('cn_title', '')[:20] for it in top_items(items, top_n)
                         if not (it.get('editor_note') or '').strip()]
        if missing_notes:
            issues.append(f"⚠️ 前{top_n}条缺 editor_note（{len(missing_notes)}条）：{missing_notes}")

    # Check 12: 反 AI 腔
    if qc.get('check_anti_cliche', True):
        hits = find_cliches(items)
        if hits:
            preview = '；'.join(f"#{i} {w}" for i, _f, w, _c in hits[:6])
            more = f"（共{len(hits)}处）" if len(hits) > 6 else ''
            issues.append(f"⚠️ AI 腔套话{more}：{preview}")
        trace_n = sum(1 for it in items for f in _CN_FIELDS
                      if AI_TRACE_RE.search(it.get(f) or ''))
        if trace_n:
            issues.append(f"⚠️ “该X”书面指代 {trace_n} 处（跑 qc_daily.py --fix-trace 可批量改）")

    # Check 13: LLM 静默降级占比
    max_fb = qc.get('max_llm_fallback_rate', 0.3)
    fb_n = sum(1 for i in items if i.get('llm_fallback'))
    fb_rate = fb_n / len(items) if items else 0
    if fb_rate > max_fb:
        issues.append(f"❌ LLM 降级率过高：{fb_n}/{len(items)} ({fb_rate:.0%}) > {max_fb:.0%}，摘要多为原文兜底，不可直接交付")

    # Check 14: 登录页／跳转页脏条目（会顶掉真实新闻的位置）
    junk = [i for i in items if is_junk(i)]
    if junk:
        preview = '；'.join((it.get('title') or it.get('url', ''))[:40] for it in junk[:3])
        issues.append(f"❌ 混入登录页/跳转页脏条目 {len(junk)} 条：{preview}")

    passed = len([i for i in issues if i.startswith('❌')]) == 0
    return passed, issues


def print_report(items: List[Dict], settings: Dict):
    """Print quality validation report."""
    passed, issues = validate_report(items, settings)
    qc = settings.get('quality_control', {})
    max_age = qc.get('max_item_age_hours',
                     settings.get('pipeline', {}).get('lookback_hours', 24) * 1.5)
    fresh = freshness_stats(items, max_age)

    print("\n" + "=" * 60)
    print("📊 质检报告")
    print("=" * 60)
    print(f"总条目数: {len(items)}")
    print(f"来源数量: {len(set(i.get('source','') for i in items))}")

    # Category stats（归一化后统计，和 HTML 里看到的分区一致）
    cat_counts = {}
    for item in items:
        cat = normalize_cat(item)
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    print(f"分类分布: {json.dumps(cat_counts, ensure_ascii=False)}")

    img_n = sum(1 for i in items if i.get('image_url'))
    print(f"配图率: {img_n}/{len(items)} ({(img_n / len(items) if items else 0):.0%})")
    print(f"时效性: {fresh['fresh']}/{fresh['total']} 条在 {max_age:.0f}h 内"
          f"（过期 {fresh['stale']}，无日期 {fresh['unknown']}，最新 {fresh['newest'][:16]}）")

    top_n = (settings.get('editor_note', {}) or {}).get('top_n', 5)
    note_n = sum(1 for it in top_items(items, top_n) if (it.get('editor_note') or '').strip())
    print(f"编辑判断: 前{top_n}条中 {note_n} 条有 editor_note")

    fb_items = [i.get('cn_title') or i.get('title', '') for i in items if i.get('llm_fallback')]
    print(f"LLM降级: {len(fb_items)} 条")
    for title in fb_items[:8]:
        print(f"  ↳ {title[:40]}")

    flagged = [(i.get('cn_title') or i.get('title', ''), (i.get('fact_check') or {}).get('issues', []))
               for i in items if (i.get('fact_check') or {}).get('issues')]
    hard_flagged = [(t, s) for t, s in flagged
                    if any('仅URL校验通过' not in x for x in s)]
    print(f"事实校对标记: {len(hard_flagged)} 条（另有 {len(flagged) - len(hard_flagged)} 条仅URL校验）")
    for title, probs in hard_flagged[:8]:
        print(f"  ↳ {title[:32]} → {'；'.join(probs)[:80]}")

    print(f"\n质检结果: {'✅ 通过' if passed else '❌ 未通过'}")
    if issues:
        print(f"问题列表 ({len(issues)}项):")
        for issue in issues:
            print(f"  {issue}")
    print("=" * 60 + "\n")
    return passed
