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
python3 build_daily.py                  # 完整流程
python3 build_daily.py --skip-collect   # 复用已采集数据
python3 build_daily.py --dry-run        # 只生成预览
python3 build_daily.py --no-calibrate   # 跳过校准（快速出稿）
python3 build_daily.py --no-editor-notes # 跳过前5条编辑判断
python3 build_daily.py --llm-probe      # 只探活 LLM 端点（30秒诊断，不采集不出稿）
python3 build_daily.py --allow-llm-failure # LLM 不可用时仍出降级稿（应急）
```

## LLM 端点探活与降级闸门

`llm_chat` 会把所有异常吞成空串。端点不可达时流水线不会报错，而是逐条退回
"原文摘要兜底"——日报照样生成、CI 照样绿灯，但标题没中文化、标签全空。
两道闸门拦这种情况：

1. **Step 0 探活**：正式采集前按 `primary → fallback` 顺序各发一次最小请求，
   选中第一个能出字的端点；全挂则 `exit 2`，不浪费十几分钟抓数据。
2. **兜底比例闸门**：出稿前统计走兜底的条目占比，超过
   `quality_control.max_llm_fallback_rate`（默认 0.3）则 `exit 3`，
   数据留在 `data/today_data.json`，排查完重跑。

备用端点用三个环境变量配置，缺一不启用：

```bash
LLM_FALLBACK_BASE_URL=https://models.inference.ai.azure.com
LLM_FALLBACK_API_KEY=$GITHUB_TOKEN
LLM_FALLBACK_MODEL=gpt-4o-mini
```

`llm_chat` 自带指数退避重试（默认 3 次，`LLM_CHAT_ATTEMPTS` 可调），
把偶发 429/连接抖动和端点彻底不可用区分开。

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

> **`LLM_BASE_URL` 必须是 GitHub runner 能访问的公网地址。**
> 公司内网 one-api（如 `tools.ai-transsion.com`）从 runner 上连不通，
> 探活会直接失败。这种情况靠 workflow 里已配好的 GitHub Models 备用端点兜住
> （用 `GITHUB_TOKEN`，无需额外密钥）；想换端点或模型，在
> Settings → Variables 里设 `LLM_FALLBACK_BASE_URL` / `LLM_FALLBACK_MODEL`。

手动触发时的输入项：

| 输入 | 作用 |
| --- | --- |
| `llm_probe_only` | 只跑端点探活，约 30 秒出结论，用于排查 CI 的 LLM 配置 |
| `dry_run` | 只生成预览，不发布 |
| `skip_collect` | 复用已采集数据 |
| `no_calibrate` | 跳过校准 + 一致性自检 |
| `allow_llm_failure` | LLM 不可用时仍出降级稿（应急） |

排查顺序建议：先用 `llm_probe_only` 确认端点，再跑完整流程。

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
