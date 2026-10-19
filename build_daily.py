#!/usr/bin/env python3
"""
AI 资讯日报 — 一键生成脚本
用法:
  python3 build_daily.py              # 完整流程：采集+解析+选稿+生成HTML
  python3 build_daily.py --skip-collect # 复用已采集数据，跳过采集步骤
  python3 build_daily.py --dry-run     # 只生成预览，不发布微信
  python3 build_daily.py --publish     # 生成后自动发布到微信公众号草稿箱
  python3 build_daily.py --no-calibrate # 跳过自校准（快速出稿）
"""
import os
import sys
import json
import argparse
import yaml
from datetime import datetime, timedelta

# Add project root to path
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
# src/ 内的模块互相引用用的是裸 import（utils.text / processors.xxx），
# 所以 src/ 本身也要在 sys.path 上，否则 ModuleNotFoundError: No module named 'utils'
sys.path.insert(0, os.path.join(_ROOT, 'src'))

from dotenv import load_dotenv
load_dotenv()

from src.collectors.rss_collector import collect_rss
from src.collectors.web_collector import collect_web
from src.collectors.hn_collector import collect_hackernews
from src.collectors.arxiv_collector import collect_arxiv
from src.collectors.github_trending_collector import collect_github_trending
from src.processors.dedup import deduplicate, cross_day_dedup
from src.processors.categorizer import assign_categories
from src.processors.summarizer import summarize_item, summarize_paper, curate_items, llm_chat
from src.processors.scorer import rank_items, select_top_items
from src.processors.calibrator import calibrate_batch, calibrate_paper, editor_notes_batch
from src.processors.fact_checker import fact_check_batch
from src.processors.story_merger import merge_stories
from src.processors.post_merge import post_summarize_merge
from src.processors.coherence_check import coherence_batch, drop_failed_items
from src.processors.content_fetcher import enrich_source_text
from src.publishers.html_publisher import generate_html, save_html
from src.publishers.wechat_publisher import publish_to_wechat
from src.utils.validator import print_report
from src.utils.image_extractor import extract_images_batch
from src.utils.text import extract_domain


def load_config(config_dir: str = 'config') -> dict:
    """Load all YAML config files."""
    config = {}
    for fname in os.listdir(config_dir):
        if fname.endswith('.yaml') or fname.endswith('.yml'):
            key = fname.rsplit('.', 1)[0]
            with open(os.path.join(config_dir, fname), 'r', encoding='utf-8') as f:
                config[key] = yaml.safe_load(f)
    return config


def collect_all(config: dict, lookback_hours: int = 24) -> list:
    """Collect from all configured sources."""
    sources = config.get('sources', {})
    all_items = []

    # 1. RSS feeds
    print("\n📡 === 采集 RSS 源 ===")
    for feed in sources.get('rss_feeds', []):
        try:
            items = collect_rss(feed, lookback_hours)
            all_items.extend(items)
        except Exception as e:
            print(f"  [!] RSS error {feed.get('name')}: {e}")

    # 2. Web sources
    print("\n🌐 === 采集网页源 ===")
    for source in sources.get('web_sources', []):
        try:
            items = collect_web(source)
            all_items.extend(items)
        except Exception as e:
            print(f"  [!] Web error {source.get('name')}: {e}")

    # 3. Hacker News
    hn_config = sources.get('hackernews', {})
    if hn_config.get('enabled', True):
        print("\n💡 === 采集 Hacker News ===")
        try:
            items = collect_hackernews(hn_config)
            all_items.extend(items)
        except Exception as e:
            print(f"  [!] HN error: {e}")

    # 4. arXiv
    arxiv_config = sources.get('arxiv', {})
    if arxiv_config.get('enabled', True):
        print("\n📄 === 采集 arXiv 论文 ===")
        try:
            items = collect_arxiv(arxiv_config)
            all_items.extend(items)
        except Exception as e:
            print(f"  [!] arXiv error: {e}")

    # 5. GitHub Trending (NEW - learned from CloudFlare project)
    gh_config = sources.get('github_trending', {})
    if gh_config.get('enabled', True):
        print("\n📦 === 采集 GitHub Trending ===")
        try:
            items = collect_github_trending(gh_config)
            all_items.extend(items)
        except Exception as e:
            print(f"  [!] GitHub Trending error: {e}")

    # Add domain info
    for item in all_items:
        if not item.get('source_domain'):
            item['source_domain'] = extract_domain(item.get('url', ''))

    print(f"\n📊 采集总计: {len(all_items)} 条原始数据")
    return all_items


def process_and_summarize(items: list, config: dict, skip_calibration: bool = False,
                          with_editor_notes: bool = True) -> list:
    """Deduplicate, summarize, categorize, score, curate, and calibrate items."""
    settings = config.get('settings', {})
    qc = settings.get('quality_control', {})
    dedup_threshold = settings.get('pipeline', {}).get('dedup_similarity_threshold', 0.65)
    categories_config = config.get('categories', {}).get('categories', [])

    # Build max_per_category dict from config
    max_per_category = {}
    for cat in categories_config:
        max_per_category[cat['key']] = cat.get('max_items', 99)

    # Step 1: Initial dedup
    print("\n🔄 === 去重 ===")
    items = deduplicate(items, dedup_threshold)

    # Step 1.5: Story merging — cluster same-event multi-source reports
    print("\n🔀 === 多源故事合并 ===")
    items, merge_stats = merge_stories(items)
    if merge_stats.get('merged_groups', 0) > 0:
        print(f"  合并了 {merge_stats['merged_groups']} 组同源报道")

    # Step 2: LLM summarization
    print("\n🤖 === LLM 生成摘要 ===")
    summarized = []
    for i, item in enumerate(items):
        print(f"  [{i+1}/{len(items)}] Processing: {item.get('title', '')[:40]}...")
        try:
            if item.get('is_paper'):
                result = summarize_paper(item)
            else:
                result = summarize_item(item)
            item.update(result)
            summarized.append(item)
        except Exception as e:
            print(f"  [!] Summarize error: {e}")
            item['cn_title'] = item.get('title', '')[:20]
            item['cn_summary'] = item.get('summary', '')[:160]
            item['tags'] = []
            item['category'] = item.get('category_hint', 'headline')
            summarized.append(item)

    # Step 3: Assign categories
    print("\n🏷️ === 分类 ===")
    items = assign_categories(summarized)

    # Step 4: Score and rank (NEW - learned from TrendRadar)
    print("\n📊 === 评分排序 ===")
    items = rank_items(items)

    # Step 5: Curate with LLM
    print("\n📋 === LLM 选题与排序 ===")
    max_items = settings.get('pipeline', {}).get('max_items', 35)
    max_per_source = qc.get('max_items_per_source', 5)
    # First try LLM curation
    curated = curate_items(items)
    # Then enforce diversity constraints
    items = select_top_items(curated, max_items=max_items,
                              max_per_source=max_per_source,
                              max_per_category=max_per_category)

    # Step 5.5: Post-summary semantic duplicate merge.
    # The Step-1.5 story_merger runs BEFORE summarization, so it only ever sees
    # English source titles and misses cross-source same-event pairs. This pass
    # re-checks on cn_title/cn_summary with an LLM judge (1 call).
    print("\n🔀 === 语义去重（摘要后）===")
    try:
        items, post_merge_stats = post_summarize_merge(items)
        if post_merge_stats.get('groups', 0) > 0:
            print(f"  折叠了 {post_merge_stats['groups']} 组同源报道")
    except Exception as e:
        print(f"  [!] Post-merge error (ignored): {e}")

    # Step 6: Self-calibration (NEW - 校准机制)
    if not skip_calibration:
        print("\n🔍 === 自校准机制 ===")
        calibrated = []
        for i, item in enumerate(items):
            print(f"  [{i+1}/{len(items)}] Calibrating: {item.get('cn_title', '')[:30]}...")
            try:
                if item.get('is_paper') and item.get('plain_explanation'):
                    cal_result = calibrate_paper(item)
                else:
                    from src.processors.calibrator import calibrate_item
                    cal_result = calibrate_item(item)
                calibrated.append(cal_result)
            except Exception as e:
                print(f"  [!] Calibration error: {e}")
                calibrated.append(item)
        items = calibrated
    else:
        print("\n⏭️ 跳过自校准（快速出稿模式）")

    # Step 6.5: Coherence self-check — verify summaries against source text
    coh_stats = {}
    if not skip_calibration:
        print("\n🔎 === 一致性自检 ===")
        items, coh_stats = coherence_batch(items)
        failed = coh_stats.get('failed', 0)
        if failed > 0:
            print(f"  ⚠️ {failed} 条摘要与原文不一致，已尝试修正")

    # Step 6.6: 剔除改不回来的"加戏"条目 —— 不让它们混进最终稿
    drop_cfg = settings.get('coherence', {}) or {}
    if drop_cfg.get('drop_fail', True):
        min_items_cfg = settings.get('pipeline', {}).get('min_items', 25)
        before = len(items)
        items, drop_stats = drop_failed_items(items, min_keep=drop_cfg.get('min_keep', min_items_cfg))
        if drop_stats['dropped']:
            print(f"  🗑 剔除严重不一致条目: {drop_stats['dropped']} 条 "
                  f"(保留 {drop_stats['remaining']}/{before})")
        if drop_stats['kept_fail']:
            print(f"  ⚠️ 剔除后会低于 {min_items_cfg} 条，"
                  f"按分数回补了 {drop_stats['kept_fail']} 条 fail 条目，需人工复核")
        # 条目数变了，重新按排名分位分级，保证三档层次仍然成立
        if len(items) != before:
            items = rank_items(items)

    # Step 7: 编辑判断 —— 模板里的"洞察"块靠 editor_note 渲染，
    # 这一步独立于自校准，--no-calibrate 快速出稿时也要有
    if with_editor_notes:
        top_n = (settings.get('editor_note', {}) or {}).get('top_n', 5)
        try:
            items, note_stats = editor_notes_batch(items, top_n=top_n)
        except Exception as e:
            print(f"  [!] 编辑判断阶段出错（已忽略）: {e}")
    else:
        print("\n⏭️ 跳过编辑判断（--no-editor-notes）")

    return items


def generate_title_digest(items: list, date_str: str) -> tuple:
    """Generate article title and digest."""
    headlines = [i for i in items if i.get('category') == 'headline']
    if not headlines:
        headlines = items[:3]

    # Try to find a hot keyword for title
    all_titles = " ".join(i.get('cn_title', '') for i in items)
    title_hint = ""
    for kw in ['DeepSeek', 'OpenAI', 'GPT', 'Claude', 'Gemini', 'Meta', '百度', '阿里', '腾讯']:
        if kw.lower() in all_titles.lower():
            title_hint = kw
            break

    date_cn = f"{date_str[5:7]}月{date_str[8:10]}日"
    if title_hint:
        title = f"AI洞察局｜{date_cn} AI日报：{title_hint}领衔重磅资讯"
    else:
        title = f"AI洞察局｜{date_cn} AI日报：今日{len(items)}条精选资讯"

    digest = f"今日{len(items)}条精选AI资讯，涵盖{len(set(i.get('source','') for i in items))}个来源。"
    if headlines:
        digest += " 重点：" + "、".join(h.get('cn_title', '')[:12] for h in headlines[:3]) + "。"
    digest = digest[:120]

    return title, digest


def main():
    parser = argparse.ArgumentParser(description='AI资讯日报生成器')
    parser.add_argument('--skip-collect', action='store_true', help='复用已采集数据')
    parser.add_argument('--dry-run', action='store_true', help='只生成预览，不发布')
    parser.add_argument('--publish', action='store_true', help='发布到微信公众号草稿箱')
    parser.add_argument('--no-calibrate', action='store_true', help='跳过自校准（快速出稿）')
    parser.add_argument('--no-editor-notes', action='store_true', help='跳过前5条的编辑判断')
    parser.add_argument('--date', type=str, default=None, help='指定日期 YYYY-MM-DD')
    args = parser.parse_args()

    # Load config
    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config')
    config = load_config(config_dir)
    settings = config.get('settings', {})

    date_str = args.date or datetime.now().strftime('%Y-%m-%d')
    output_dir = settings.get('output', {}).get('dir', 'data')
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), output_dir)

    print(f"═══════════════════════════════════════════════════════")
    print(f"  📰 AI 资讯日报生成器 — {date_str}")
    print(f"═══════════════════════════════════════════════════════\n")

    # Step 1: Collect
    raw_data_path = os.path.join(output_dir, 'raw_collected.json')
    if args.skip_collect and os.path.exists(raw_data_path):
        print("📂 复用已采集数据...")
        with open(raw_data_path, 'r', encoding='utf-8') as f:
            items = json.load(f)
    else:
        items = collect_all(config, settings.get('pipeline', {}).get('lookback_hours', 24))
        os.makedirs(output_dir, exist_ok=True)
        with open(raw_data_path, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    if not items:
        print("❌ 未采集到任何数据！")
        sys.exit(1)

    # Step 1.5: 正文补全 —— 摘要过短的条目先抓原文正文，抓不到才丢弃。
    # 目的：从源头消灭"凭标题脑补"导致的编造（一致性自检 fail）。
    st_cfg = settings.get('source_text', {})
    if st_cfg.get('enabled', True):
        print("\n📄 === 原文正文补全 ===")
        before = len(items)
        items, st_stats = enrich_source_text(
            items,
            enabled=True,
            min_chars=st_cfg.get('min_chars', 50),
            max_workers=st_cfg.get('max_workers', 8),
            timeout=st_cfg.get('timeout', 20),
        )
        print(f"  原文不足(<{st_cfg.get('min_chars', 50)}字): {st_stats['thin']} 条")
        print(f"  ✅ 正文补全成功: {st_stats['enriched']} 条")
        print(f"  🗑 无法获取正文已剔除: {st_stats['dropped']} 条")
        print(f"  📊 保留: {st_stats['kept']}/{before} 条")
        min_items_cfg = settings.get('pipeline', {}).get('min_items', 25)
        if len(items) < min_items_cfg:
            print(f"  ⚠️ 补全后仅剩 {len(items)} 条，低于 min_items({min_items_cfg})，"
                  f"今日稿可能不够丰满，可考虑放宽 source_text.min_chars")

    # Step 2: Process & Summarize
    items = process_and_summarize(items, config, skip_calibration=args.no_calibrate,
                                  with_editor_notes=not args.no_editor_notes)

    # Step 3: Save today_data.json (for manual editing & gen_daily.py)
    today_data_path = os.path.join(output_dir, 'today_data.json')
    with open(today_data_path, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已保存: {today_data_path}")

    # Step 3.5: Extract images from article URLs
    img_config = settings.get('image_extraction', {})
    if img_config.get('enabled', True):
        print("\n🖼️ === 提取配图 ===")
        items = extract_images_batch(
            items,
            max_fetches=img_config.get('max_fetches', 60),
            validate=img_config.get('validate_urls', True),
        )

    # Step 3.6: 多重事实校对（真实性+时效性+准确性）
    fc_config = settings.get('fact_check', {})
    if fc_config.get('enabled', True):
        print("\n🔎 === 多重事实校对 ===")
        lookback = fc_config.get('lookback_hours', 48)
        items, fc_stats = fact_check_batch(items, lookback_hours=lookback)
        # 保存校对结果到 today_data.json
        with open(today_data_path, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        flagged = fc_stats.get('flagged', 0)
        if flagged > 0:
            print(f"  ⚠️ {flagged} 条新闻被标记，需人工审核")
        print(f"  ✅ 通过: {fc_stats.get('passed', 0)}/{fc_stats.get('total', 0)}")

    # Step 3.7: Save quality report (observable workflow, learned from TrendPublish)
    quality_report = {
        'date': date_str,
        'pipeline_steps': [
            {'step': 'collect', 'items': len(items)},
            {'step': 'dedup', 'status': 'done'},
            {'step': 'story_merge', 'status': 'done'},
            {'step': 'summarize', 'status': 'done'},
            {'step': 'calibrate', 'status': 'done'},
            {'step': 'coherence_check', 'status': 'done'},
            {'step': 'fact_check', 'status': 'done'},
        ],
        'total_items': len(items),
        'signal_grades': {
            'heavy': len([i for i in items if i.get('signal_grade') == '重磅']),
            'noticeable': len([i for i in items if i.get('signal_grade') == '值得关注']),
            'routine': len([i for i in items if i.get('signal_grade') == '常规']),
        },
        'fact_check_stats': fc_stats if fc_config.get('enabled', True) else {},
    }
    quality_report_path = os.path.join(output_dir, 'quality_report.json')
    with open(quality_report_path, 'w', encoding='utf-8') as f:
        json.dump(quality_report, f, ensure_ascii=False, indent=2)
    print(f"\n📋 质量报告已保存: {quality_report_path}")

    # Step 4: Quality check
    passed = print_report(items, settings)

    if not passed and not args.dry_run:
        print("⚠️ 质检未通过！建议检查 today_data.json 后重新运行 gen_daily.py")
        print("  使用 --dry-run 可跳过发布步骤")

    # Step 5: Generate HTML
    print("\n📄 === 生成 HTML ===")
    html = generate_html(items, date_str)
    html_path = save_html(html, output_dir, date_str)

    # Save previous titles for cross-day dedup
    prev_titles_path = os.path.join(output_dir, 'previous_titles.json')
    prev_titles = [item.get('cn_title', item.get('title', '')) for item in items]
    with open(prev_titles_path, 'w', encoding='utf-8') as f:
        json.dump({'titles': prev_titles, 'date': date_str}, f, ensure_ascii=False, indent=2)

    # Step 6: Publish to WeChat (if requested)
    if args.publish and not args.dry_run:
        print("\n📤 === 发布到微信公众号 ===")
        title, digest = generate_title_digest(items, date_str)
        print(f"  标题: {title}")
        print(f"  摘要: {digest}")
        success = publish_to_wechat(html, title, digest)
        if success:
            print("  ✅ 已创建草稿，请到公众号后台审核发布")
        else:
            print("  ❌ 发布失败，请检查配置")

    print(f"\n{'='*55}")
    print(f"  ✅ 日报生成完成！")
    print(f"  📄 HTML: {html_path}")
    print(f"  📋 JSON: {today_data_path}")
    print(f"{'='*55}")

    # Print highlights
    print(f"\n📌 今日重点:")
    headlines = [i for i in items if i.get('category') == 'headline']
    for h in headlines[:5]:
        print(f"  • {h.get('cn_title', '')} — {h.get('source', '')}")
    if not headlines:
        for item in items[:5]:
            print(f"  • {item.get('cn_title', '')} — {item.get('source', '')}")


if __name__ == '__main__':
    main()
