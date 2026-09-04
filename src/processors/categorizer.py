"""Categorization processor — assigns items to news categories."""
import re
from typing import Dict, List


CATEGORY_KEYWORDS = {
    'investment': ['融资', '投资', '收购', '并购', '估值', '募资', 'A轮', 'B轮', 'C轮', 'IPO', 'funding', 'acquisition', 'investment', 'raises', 'valuation', 'series'],
    'policy': ['政策', '监管', '法规', '合规', '禁令', '监管局', '工信部', 'regulation', 'policy', 'ban', 'law', 'compliance', 'EU AI Act', '监管局'],
    'technology': ['技术', '架构', '训练', '模型', '算法', 'benchmark', 'SOTA', 'technique', 'architecture', 'training', 'algorithm', 'breakthrough'],
    'product': ['发布', '上线', '推出', '更新', 'launch', 'release', 'product', 'update', 'unveils', 'introduces', 'rolls out', '上线', '发布'],
    'bigtech': ['OpenAI', 'Google', 'DeepMind', 'Meta', '微软', 'Microsoft', '百度', '阿里', '腾讯', '字节', 'ByteDance', 'Amazon', 'Nvidia', '苹果', 'Apple', 'Anthropic', '华为'],
    'opinion': ['观点', '认为', '预测', '表示', '称', 'says', 'predicts', 'believes', 'warns', 'opinion', '观点', '认为'],
    'paper': ['arXiv', '论文', 'paper', 'research', 'study', '研究'],
    'opensource': ['GitHub', '开源', 'open source', 'repo', 'release', 'MIT license', 'Apache'],
    'event': ['会议', '峰会', '大会', '活动', 'conference', 'summit', 'event', 'meetup', '展览'],
}


def categorize_by_keywords(item: Dict) -> str:
    """Assign category based on keyword matching."""
    if item.get('is_paper'):
        return 'paper'

    text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('cn_title', '')} {item.get('cn_summary', '')}".lower()

    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text)
        if score > 0:
            scores[cat] = score

    if scores:
        return max(scores, key=scores.get)

    return 'headline'


def assign_categories(items: List[Dict]) -> List[Dict]:
    """Ensure all items have a category assigned."""
    for item in items:
        if 'category' not in item or not item['category']:
            item['category'] = categorize_by_keywords(item)
    return items
