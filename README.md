# AI 资讯日报生成器

每日自动采集多源 AI 资讯，经多源合并、加权评分、LLM 摘要（反 AI 腔）、校准、一致性自检、三重事实校对后，生成适配公众号格式的 HTML 日报。

## Pipeline

```
collect → dedup → story_merge → summarize → categorize → score
→ curate → calibrate(AI腔检测) → coherence_check → extract_images
→ fact_check → quality_report → generate_html
```

## 本地运行

```bash
pip install -r requirements.txt
cp .env.example .env  # 填入 LLM_API_KEY
python3 build_daily.py                 # 完整流程
python3 build_daily.py --skip-collect  # 复用已采集数据
python3 build_daily.py --dry-run       # 只生成预览
python3 build_daily.py --no-calibrate  # 跳过校准（快速出稿）
```

## GitHub Actions 自动化

每天北京时间 07:30 自动执行，无需电脑开机。

### 配置步骤

1. Fork 或推送到 GitHub 仓库
2. Settings → Secrets and variables → Actions，添加：
   - `LLM_API_KEY`：GLM API Key
   - `LLM_BASE_URL`：`https://open.bigmodel.cn/api/paas/v4`
   - `LLM_MODEL`：`glm-5.2`
   - `WECHAT_APP_ID`（可选，自动发布到公众号草稿箱）
   - `WECHAT_APP_SECRET`（可选）
3. 每天 07:30 自动生成报告，commit 到 `data/` 目录
4. 也可在 Actions 页面手动触发（workflow_dispatch）

## 数据源

- AIHOT精选、AIbase头条（必含关注度前5）
- 量子位RSS、IT之家RSS、36氪、机器之心
- TechCrunch、The Verge、Ars Technica、MIT Tech Review
- Hacker News、arXiv cs.AI、GitHub Trending

## 信号分级

- **重磅**（score≥55）：重大发布/融资/突破
- **值得关注**（score≥25）：行业动态/产品更新
- **常规**（score<25）：日常快讯

## 反 AI 腔

禁止"此举旨在""标志着""被视为""引发业界""赋能""助力"等套话，要求像科技记者写作。

## 学习的 GitHub 项目

- [AI_News_Digest](https://github.com/chengjialu8888/AI_News_Digest) — 5道QA Gate、信号分级
- [ai-daily-news](https://github.com/Alionkissadeer/ai-daily-news) — 三层去重、log2评分
- [claude-rss-news-digest](https://github.com/SeanLF/claude-rss-news-digest) — COHERENCE自检+修复
- [ai-scan](https://github.com/YMaxwellHayes/ai-scan) — 加权评分、指数衰减
- [ai-trend-publish](https://github.com/liyown/ai-trend-publish) — 可观测工作流
