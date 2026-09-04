#!/usr/bin/env python3
"""Test the pipeline with realistic mock data — verify HTML + image extraction logic."""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.publishers.html_publisher import generate_html, save_html
from src.utils.validator import print_report


# Mock data with realistic news items
MOCK_ITEMS = [
    {
        "cn_title": "OpenAI发布GPT-5正式版，推理能力大幅提升",
        "cn_summary": "OpenAI于9月3日正式发布GPT-5模型，在数学推理、代码生成和多语言理解方面均有显著提升。新模型采用改进的RLHF训练方法，支持128K上下文窗口，API价格较GPT-4降低40%。",
        "tags": ["GPT-5", "OpenAI", "大模型"],
        "source": "TechCrunch",
        "url": "https://techcrunch.com/2026/09/03/openai-gpt5",
        "category": "headline",
        "image_url": "https://images.unsplash.com/photo-1677442136019-21780ecaddd5?w=800",
    },
    {
        "cn_title": "智谱AI完成D轮融资50亿元，估值达500亿",
        "cn_summary": "智谱AI宣布完成D轮融资，金额达50亿元人民币，由国资基金领投，君联资本等知名机构跟投。资金将主要用于GLM系列大模型持续训练、行业应用落地以及全球化生态建设。完成本轮融资后，公司估值升至500亿元，跻身国内AI独角兽前列。",
        "tags": ["智谱AI", "融资", "GLM"],
        "source": "36氪",
        "url": "https://36kr.com/p/2808903456",
        "category": "investment",
        "image_url": "",
    },
    {
        "cn_title": "Meta开源Llama 4：支持多模态的70B参数模型",
        "cn_summary": "Meta正式开源Llama 4系列模型，包含8B、70B和405B三个版本。新模型原生支持图文多模态输入，在MMLU和HumanEval基准上超越GPT-4o，可免费用于研究和商业用途。",
        "tags": ["Llama4", "开源", "Meta"],
        "source": "Hacker News",
        "url": "https://news.ycombinator.com/item?id=41567890",
        "category": "opensource",
        "image_url": "https://images.unsplash.com/photo-1620712947044-e9dddaf4e3a8?w=800",
    },
    {
        "cn_title": "MoE架构的缩放定律研究",
        "cn_summary": "Google DeepMind最新论文系统研究了MoE架构的Scaling Laws，发现专家数量与总参数量的最优比例随计算预算变化。实验覆盖1B到100B参数规模，为MoE模型设计提供了理论指导。",
        "tags": ["MoE", "ScalingLaws", "DeepMind"],
        "source": "arXiv cs.AI",
        "url": "https://arxiv.org/abs/2609.01234",
        "category": "paper",
        "is_paper": True,
        "one_liner": "首次为MoE架构建立完整的Scaling Laws理论框架",
        "key_points": [
            "MoE最优专家数随计算预算超线性增长",
            "稀疏激活模式显著影响推理效率",
            "提出了专家分配的帕累托最优策略",
        ],
        "plain_explanation": "就像公司里要不要多招专家还是多招通才——这篇论文告诉你，预算越大，专家越多越划算，但需要合理分配任务。",
        "why_care": "MoE是大模型未来降本增效的关键架构，这项研究直接影响下一代GPT和Llama的设计方向。",
        "image_url": "",
    },
    {
        "cn_title": "苹果发布Apple Intelligence 2.0，端侧模型升级",
        "cn_summary": "苹果在秋季发布会上推出Apple Intelligence 2.0，端侧3B模型推理速度提升2倍，新增实时翻译、智能摘要和跨App操作能力。所有处理在本地完成，隐私保护进一步加强。",
        "tags": ["苹果", "端侧AI", "AppleIntelligence"],
        "source": "The Verge",
        "url": "https://www.theverge.com/2026/9/3/apple-intelligence-2",
        "category": "product",
        "image_url": "https://images.unsplash.com/photo-1611532336124-5510aec2b9c8?w=800",
    },
    {
        "cn_title": "李开复：AGI将在2027年前实现，中国企业需加快布局",
        "cn_summary": "李开复在世界人工智能大会发表主题演讲，认为AGI将在2027年前实现，呼吁中国企业加大基础研究投入。他特别强调多模态理解和具身智能是AGI突破的关键方向，中国应充分发挥制造优势和数据优势，在应用层和基础设施层同步布局。",
        "tags": ["李开复", "AGI", "WAIC"],
        "source": "量子位",
        "url": "https://www.qbitai.com/2026/09/156789.html",
        "category": "opinion",
        "image_url": "",
    },
    {
        "cn_title": "国务院发布《生成式AI服务管理办法》修订版",
        "cn_summary": "国务院正式发布《生成式人工智能服务管理暂行办法》修订版，新增算法备案、数据来源审计、内容标识和未成年人保护等要求。新规将于2026年10月1日正式施行，未完成备案的生成式AI产品不得面向公众上线运营，违者将面临处罚。",
        "tags": ["政策监管", "算法备案", "数据审计"],
        "source": "IT之家",
        "url": "https://www.ithome.com/0/798/abc.htm",
        "category": "policy",
        "image_url": "https://images.unsplash.com/photo-1454165804606-c3d57bc86c42?w=800",
    },
    {
        "cn_title": "DeepSeek-V3.5发布：671B参数MoE架构，推理成本仅GPT-4的1/10",
        "cn_summary": "深度求索发布DeepSeek-V3.5模型，采用671B参数MoE架构，每次推理仅激活37B参数。在AIME和Codeforces榜单上达到o1级别，API价格仅为GPT-4 Turbo的十分之一。",
        "tags": ["DeepSeek", "MoE", "推理成本"],
        "source": "AIbase",
        "url": "https://www.aibase.com/zh/news/12345",
        "category": "technology",
        "image_url": "https://images.unsplash.com/photo-1639762681485-3a5cd4c2c6f0?w=800",
    },
    {
        "cn_title": "字节跳动豆包大模型日均调用量突破2万亿token",
        "cn_summary": "字节跳动公布豆包大模型最新运营数据，日均调用量突破2万亿token，企业客户超50万家覆盖电商、教育、金融等领域。豆包Pro版本支持256K上下文窗口，API价格下调至0.0008元/千token，位居行业最低水平，加速大模型普惠化进程。",
        "tags": ["字节跳动", "豆包", "大模型"],
        "source": "36氪",
        "url": "https://36kr.com/p/2808903457",
        "category": "bigtech",
        "image_url": "https://images.unsplash.com/photo-1611162617213-7ccdbf5d6d51?w=800",
    },
    {
        "cn_title": "WAIC 2026将于11月在上海举办，参展企业超500家",
        "cn_summary": "2026世界人工智能大会（WAIC）将于11月15-17日在上海举办，展区面积达10万平方米，已确认参展企业超500家。大会设置大模型、具身智能、AI芯片、AI制药等八大主题论坛，邀请图灵奖得主和顶尖企业CEO发表演讲，预计参会人数超10万。",
        "tags": ["WAIC", "上海", "展会"],
        "source": "机器之心",
        "url": "https://www.jiqizhixin.com/articles/2026-09-03",
        "category": "event",
        "image_url": "",
    },
]


def main():
    date_str = "2026-09-03"
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

    # Mock settings for validation
    settings = {
        'pipeline': {'min_items': 5, 'max_items': 40, 'summary_min_chars': 90, 'summary_max_chars': 200, 'tags_per_item': 3},
        'quality_control': {'require_source_diversity': True, 'min_sources': 3, 'check_summary_length': True, 'check_no_duplicates': True, 'check_no_english_titles': True, 'check_tags_count': True, 'max_items_per_source': 5, 'require_aibase_top5': False},
    }

    print(f"📊 Mock data: {len(MOCK_ITEMS)} items")

    # Validate
    print_report(MOCK_ITEMS, settings)

    # Generate HTML
    html = generate_html(MOCK_ITEMS, date_str)
    html_path = save_html(html, output_dir, date_str)

    # Stats
    img_count = sum(1 for i in MOCK_ITEMS if i.get('image_url'))
    print(f"\n✅ 测试完成！")
    print(f"📄 HTML: {html_path}")
    print(f"📊 条目数: {len(MOCK_ITEMS)}")
    print(f"🖼️ 配图数: {img_count}/{len(MOCK_ITEMS)}")
    print(f"📡 来源数: {len(set(i.get('source','') for i in MOCK_ITEMS))}")


if __name__ == '__main__':
    main()
