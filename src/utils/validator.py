"""Quality validation and calibration mechanism."""
import json
from typing import Dict, List, Tuple


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

    # Check 4: English titles must be translated
    if qc.get('check_no_english_titles', True):
        for i, item in enumerate(items):
            title = item.get('cn_title', '')
            if title and all(ord(c) < 128 for c in title if c.isalpha()):
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

    # Check 7: Category coverage
    categories_present = set(item.get('category', '') for item in items)
    required_cats = {'headline', 'paper'}
    missing = required_cats - categories_present
    if missing:
        issues.append(f"⚠️ 缺少必要分类：{missing}")

    # Check 8: Image coverage
    items_with_images = sum(1 for i in items if i.get('image_url', ''))
    image_ratio = items_with_images / len(items) if items else 0
    if image_ratio < 0.6:
        issues.append(f"⚠️ 配图率偏低：{items_with_images}/{len(items)} ({image_ratio:.0%})，建议≥60%")

    passed = len([i for i in issues if i.startswith('❌')]) == 0
    return passed, issues


def print_report(items: List[Dict], settings: Dict):
    """Print quality validation report."""
    passed, issues = validate_report(items, settings)
    print("\n" + "=" * 60)
    print("📊 质检报告")
    print("=" * 60)
    print(f"总条目数: {len(items)}")
    print(f"来源数量: {len(set(i.get('source','') for i in items))}")

    # Category stats
    cat_counts = {}
    for item in items:
        cat = item.get('category', 'unknown')
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    print(f"分类分布: {json.dumps(cat_counts, ensure_ascii=False)}")

    print(f"\n质检结果: {'✅ 通过' if passed else '❌ 未通过'}")
    if issues:
        print(f"问题列表 ({len(issues)}项):")
        for issue in issues:
            print(f"  {issue}")
    print("=" * 60 + "\n")
    return passed
