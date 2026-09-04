# AI资讯日报 — 改进记录

## 已安装的 Codex Skills

| Skill | 用途 |
|-------|------|
| playwright | 抓取JS渲染页面（36kr、AIbase等无RSS站点） |
| jupyter-notebook | 数据分析、内容质量评估 |
| screenshot | 网页截图、配图获取 |

安装路径: `~/.codex/skills/`

## 参考学习的 GitHub 项目

### 1. CloudFlare-AI-Insight-Daily (justlovemaki)
- **学习点**: 数据源分离架构（量子位、机器之心、AIbase、新智元各自独立模块）
- **学习点**: 多步摘要prompt策略（StepOne→Simplify→StepTwo→StepThree→DailyAnalysis）
- **学习点**: GitHub Trending采集模式（批量翻译项目描述）
- **学习点**: 论文采集（HuggingFace Papers + arXiv双源）

### 2. TrendRadar (SANSAN0)
- **学习点**: 新闻权重计算公式（rank_weight + frequency_weight + hotness_weight）
- **学习点**: 关键词筛选 vs AI智能筛选双模式
- **学习点**: 新鲜度过滤（max_age_days配置）
- **学习点**: 增量/当日/当前三种报告模式
- **学习点**: 多平台热榜聚合（微博、知乎、B站、百度等）

### 3. awesome-ai-news (taielab)
- **学习点**: 30+ AI新闻工具的分类和对比
- **学习点**: 推荐数据源列表（ai-news-radar、clawfeed、FeedCraft等）

## 新增功能

### 1. GitHub Trending 采集器 (`src/collectors/github_trending_collector.py`)
- 抓取 GitHub Trending 页面（Python + Jupyter Notebook 语言）
- AI关键词过滤（LLM、GPT、transformer、agent等）
- 提取仓库信息：名称、描述、语言、今日Star数、总Star数
- 对应"开源热榜"栏目

### 2. 新闻权重评分系统 (`src/processors/scorer.py`)
- 综合评分：来源权重 × 20 + HN分数 + GitHub Star + 时效性 + 关键词重要性 + AIbase必选加成
- 按分数降序排序
- 多样性约束：单来源上限5条，单栏目上限可配

### 3. 自校准机制 (`src/processors/calibrator.py`)
- LLM二次质检：准确性、完整性、语言规范、字数控制、分类准确性、标签质量
- 发现问题自动修正，记录修正日志
- 论文专用校准（标题翻译、一句话总结、核心要点、通俗解读、为什么值得关注）
- `--no-calibrate` 可跳过（快速出稿模式）

### 4. 增强的 LLM 摘要 Prompt
- 核心原则：忠于原文、通俗易懂、信息密度高、流畅自然、专业准确
- 论文解读：通俗解读+类比、引人入胜的"为什么值得关注"
- GitHub项目：自动描述项目用途和亮点
- 选题排序：参考评分但允许LLM调整，AIbase必选

### 5. 扩充数据源
- 新增：新智元、VentureBeat AI、The Rundown AI、36氪AI板块、IT桔子创投
- AIbase权重提升至1.5（确保必选）
- GitHub Trending配置（每日、Python/Jupyter语言）

## 使用方式

```bash
# 完整流程（含自校准）
python3 build_daily.py

# 快速出稿（跳过自校准）
python3 build_daily.py --no-calibrate

# 复用已采集数据
python3 build_daily.py --skip-collect

# 只生成预览
python3 build_daily.py --dry-run

# 发布到微信草稿箱
python3 build_daily.py --publish
```

## 质检流程

1. **采集**: RSS + 网页抓取 + HN API + arXiv API + GitHub Trending
2. **去重**: 三层去重（标题相似度 + URL + 跨天去重）
3. **摘要**: GLM-5.2生成中文标题、摘要、标签、分类
4. **评分**: 综合权重排序
5. **选题**: LLM选题 + 多样性约束
6. **校准**: LLM二次质检纠错 ← 新增
7. **配图**: 从原文URL提取图片（6策略级联）
8. **质检**: 摘要长度、去重、英文标题、来源多样性、标签数、配图覆盖率
9. **生成HTML**: 公众号适配格式
