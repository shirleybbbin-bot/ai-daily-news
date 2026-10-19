#!/usr/bin/env python3
"""离线质检：不联网、不采集，只判断 data/today_data.json 是否达到交付门槛。

用法:
  python3 qc_daily.py                                   # 质检 data/today_data.json
  python3 qc_daily.py data/today_data.json --date 2026-10-19
  python3 qc_daily.py --fix-trace                       # 顺手把"该模型"改成"这款模型"并写回
  python3 qc_daily.py --json                            # 额外输出机器可读结果

退出码: 0=通过（可能有 ⚠️ 提示）  1=有 ❌ 硬伤，不可直接交付
"""
import argparse
import json
import os
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
# src/ 内模块之间用裸 import（utils.text / publishers.xxx），src/ 需在 sys.path 上
sys.path.insert(0, os.path.join(_ROOT, 'src'))

import yaml

from src.utils.validator import (
    print_report, validate_report, freshness_stats, find_cliches,
    fix_ai_trace, normalize_cat, top_items, AI_TRACE_RE, _CN_FIELDS,
)


def load_settings() -> dict:
    config_path = os.path.join(_ROOT, 'config', 'settings.yaml')
    try:
        with open(config_path, encoding='utf-8') as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def load_items(json_path: str) -> list:
    with open(json_path, encoding='utf-8') as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        return data.get('items', [])
    return data


def apply_trace_fix(items: list) -> int:
    """把所有中文字段里的"该X"改成带量词说法，返回改动处数。"""
    changed = 0
    for item in items:
        for field in _CN_FIELDS:
            text = item.get(field) or ''
            fixed = fix_ai_trace(text)
            if fixed != text:
                changed += len(AI_TRACE_RE.findall(text))
                item[field] = fixed
    return changed


def main():
    parser = argparse.ArgumentParser(description='离线质检 today_data.json')
    parser.add_argument('json_path', nargs='?',
                        default=os.path.join('data', 'today_data.json'),
                        help='数据文件路径（默认 data/today_data.json）')
    parser.add_argument('--date', default=None, help='日报日期 YYYY-MM-DD（默认今天）')
    parser.add_argument('--fix-trace', action='store_true',
                        help='把"该模型/该功能"等书面指代改成口语说法并写回')
    parser.add_argument('--json', action='store_true', help='额外输出机器可读结果')
    args = parser.parse_args()

    if not os.path.exists(args.json_path):
        print(f"❌ 数据文件不存在: {args.json_path}")
        print("   云端产物还没取回本地，先执行: git pull origin main")
        return 2

    settings = load_settings()
    items = load_items(args.json_path)
    if not items:
        print(f"❌ {args.json_path} 里没有条目")
        return 1

    date_str = args.date or datetime.now().strftime('%Y-%m-%d')
    mtime = datetime.fromtimestamp(os.path.getmtime(args.json_path))
    qc = settings.get('quality_control', {})
    max_age = qc.get('max_item_age_hours',
                     settings.get('pipeline', {}).get('lookback_hours', 24) * 1.5)
    fresh = freshness_stats(items, max_age)

    print(f"📅 目标日期: {date_str}")
    print(f"📄 数据文件: {args.json_path}（更新于 {mtime:%Y-%m-%d %H:%M}）")
    stale_file = mtime.strftime('%Y-%m-%d') != date_str
    if stale_file:
        print(f"⚠️ 数据文件不是 {date_str} 生成的，可能是旧稿：确认云端是否已跑完并 git pull")

    if args.fix_trace:
        changed = apply_trace_fix(items)
        if changed:
            backup = f"{args.json_path}.bak-{date_str}"
            if not os.path.exists(backup):
                with open(backup, 'w', encoding='utf-8') as fh:
                    json.dump(load_items(args.json_path), fh, ensure_ascii=False, indent=2)
            with open(args.json_path, 'w', encoding='utf-8') as fh:
                json.dump(items, fh, ensure_ascii=False, indent=2)
            print(f"🔧 已修正 {changed} 处“该X”书面指代，写回 {args.json_path}（备份 {backup}）")
        else:
            print("🔧 未发现“该X”书面指代，无需修改")

    passed = print_report(items, settings)

    if args.json:
        cats = {}
        for item in items:
            key = normalize_cat(item)
            cats[key] = cats.get(key, 0) + 1
        sources = {}
        for item in items:
            src = item.get('source', '')
            sources[src] = sources.get(src, 0) + 1
        img_n = sum(1 for i in items if i.get('image_url'))
        _, issues = validate_report(items, settings)
        result = {
            'date': date_str,
            'file_stale': stale_file,
            'passed': passed,
            'items': len(items),
            'sources': len(sources),
            'top_sources': sorted(sources.items(), key=lambda kv: -kv[1])[:5],
            'categories': cats,
            'image_ratio': round(img_n / len(items), 3),
            'freshness': fresh,
            'aibase_items': sum(1 for i in items
                                if 'aibase' in ((i.get('source_domain') or '') + (i.get('source') or '')).lower()),
            'llm_fallback': sum(1 for i in items if i.get('llm_fallback')),
            'editor_notes': sum(1 for it in top_items(items, 5) if (it.get('editor_note') or '').strip()),
            'cliche_hits': len(find_cliches(items)),
            'fact_check_flagged': sum(1 for i in items
                                      if any('仅URL校验通过' not in x
                                             for x in (i.get('fact_check') or {}).get('issues', []))),
            'blocking': [i for i in issues if i.startswith('❌')],
            'warnings': [i for i in issues if not i.startswith('❌')],
        }
        print("JSON_RESULT " + json.dumps(result, ensure_ascii=False))

    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
