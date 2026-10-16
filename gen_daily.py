#!/usr/bin/env python3
"""
从 JSON 数据重新生成 HTML
用法:
  python3 gen_daily.py today_data.json              # 重新生成HTML
  python3 gen_daily.py today_data.json --extract-images  # 重新提取配图
"""
import os
import sys
import json
import argparse
from datetime import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
# src/ 内模块之间用裸 import（utils.text / processors.xxx），src/ 需在 sys.path 上
sys.path.insert(0, os.path.join(_ROOT, 'src'))

from src.publishers.html_publisher import generate_html, save_html
from src.utils.validator import print_report


def main():
    parser = argparse.ArgumentParser(description='从JSON重新生成HTML')
    parser.add_argument('json_path', nargs='?', default=os.path.join('data', 'today_data.json'),
                        help='JSON数据文件路径')
    parser.add_argument('--extract-images', action='store_true',
                        help='重新提取配图（覆盖已有的image_url）')
    parser.add_argument('--date', type=str, default=None, help='指定日期 YYYY-MM-DD')
    args = parser.parse_args()

    if not os.path.exists(args.json_path):
        print(f"❌ 文件不存在: {args.json_path}")
        sys.exit(1)

    with open(args.json_path, 'r', encoding='utf-8') as f:
        items = json.load(f)

    date_str = args.date or datetime.now().strftime('%Y-%m-%d')

    # Load settings for validation
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'settings.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        settings = {'settings': yaml.safe_load(f)}

    print(f"📂 加载数据: {args.json_path} ({len(items)}条)")

    # Optional: re-extract images
    if args.extract_images:
        from src.utils.image_extractor import extract_images_batch
        print("\n🖼️ === 重新提取配图 ===")
        # Clear existing image_url to force re-extraction
        for item in items:
            item.pop('image_url', None)
        items = extract_images_batch(items, validate=settings.get('image_extraction', {}).get('validate_urls', True))
        # Save updated JSON
        with open(args.json_path, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"💾 已更新配图数据: {args.json_path}")

    # Quality check
    print_report(items, settings)

    # Generate HTML
    html = generate_html(items, date_str)
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    html_path = save_html(html, output_dir, date_str)

    # Count images
    img_count = sum(1 for i in items if i.get('image_url'))
    print(f"\n✅ HTML已生成: {html_path}")
    print(f"📊 条目数: {len(items)}")
    print(f"🖼️ 配图数: {img_count}/{len(items)}")
    print(f"📡 来源数: {len(set(i.get('source','') for i in items))}")


if __name__ == '__main__':
    main()
