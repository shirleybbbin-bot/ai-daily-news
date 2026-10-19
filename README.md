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

python3 qc_daily.py                       # 离线质检 data/today_data.json（不联网、不采集）
python3 qc_daily.py --date 2026-10-19 --json   # 指定日期 + 机器可读结果
python3 qc_daily.py --fix-trace           # 把"该模型/该功能"改成"这款模型/这一功能"并写回
python3 gen_daily.py data/today_data.json --date 2026-10-19  # 改完 JSON 重新出 HTML
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

## 本地 ↔ 云端同步

云端每天把 `data/today_data.json` 和 `data/ai-daily-*.html` 提交回 `main`，本地也会生成
同名文件。于是两个方向都会卡：

- `git pull` 报 `untracked working tree files would be overwritten by merge`
- `git push` 报 non-fast-forward，云端那次 push 被拒，当天稿子只能去 Artifacts 捞

用脚本代替手敲，它按固定顺序处理：

    ./scripts/sync_and_push.sh              # 实际执行
    ./scripts/sync_and_push.sh --dry-run    # 只报告会发生什么

流程：拦住未提交改动 → `fetch` → 把与云端撞车的本地未跟踪产物挪进
`data/.local-backup/<时间戳>/`（已 gitignore，不会被误提交）→ `rebase` → `push`。
rebase 真冲突时会停下来并打印 `git rebase --abort` 的退路，不会留下半吊子状态。

云端侧同样做了加固：push 被拒时自动 `fetch` + `rebase` 重试 3 次，仍失败才让 job 变红，
并明确提示去 Artifacts 下载当天产物。

## 离线质检（qc_daily.py）

不联网、不采集，只判断 `data/today_data.json` 能不能交付。退出码 0=通过（可能带 ⚠️），
1=有 ❌ 硬伤，2=数据文件不存在。阈值都在 `config/settings.yaml` 的 `quality_control` 下。

| 检查项 | 级别 | 说明 |
| --- | --- | --- |
| 条目数 / 摘要 90-160 字 / 标签数 | ❌/⚠️ | 读 `pipeline.min_items`、`summary_min_chars` 等 |
| 重复标题、重复链接 | ❌ | 多源同事件应已合并成单卡片 |
| 英文标题未中文化 | ❌ | `lang=en` 与 Paper 都要中文标题 |
| 来源多样性 / 单源上限 | ❌/⚠️ | `min_sources`、`max_items_per_source` |
| 分类覆盖 | ⚠️ | 用发布器同一套归一化，缺 `required_categories` 里的项会列出 |
| 配图率 | ⚠️ | `min_image_ratio`（默认 0.5），图取原文 og:image |
| **时效性** | ❌ | 有日期的条目全落在 `max_item_age_hours`（默认 36h）外即判定为旧数据，专治"拿昨天的稿当今天发" |
| **AIbase 前 5** | ❌/⚠️ | `require_aibase_top5` 之前在 settings 里是死配置，现在真的会查 |
| 前 5 条 editor_note | ⚠️ | 模板靠它渲染「洞察：」块 |
| **反 AI 腔** | ⚠️ | 套话黑名单 + "该X"书面指代计数，`--fix-trace` 可批量改 |
| LLM 降级率 | ❌ | 超过 `max_llm_fallback_rate` 判不可交付 |
| 登录页/跳转页脏条目 | ❌ | 复用 `dedup.is_junk_item` |

报告里还会打印时效性明细、配图率、编辑判断覆盖、`llm_fallback` 条目与 `fact_check`
标记项（区分"硬问题"和"仅 URL 校验通过"）。`build_daily.py` 出稿前跑的是同一套检查。

## 本地心跳（Codex 定时任务）

云端 Actions 是唯一的采集与生成执行器。本地 Codex 心跳每天 08:00 触发，
沙箱只读且禁网，因此它只承担三件事：

1. 校验云端产物是否到位（`data/ai-daily-<今天>.html` + `today_data.json`）；
   缺失就汇报，并给出 `git pull` / `gh run view --log-failed` 等命令与退出码解释
2. 离线跑质量门槛（条数、分类覆盖、来源多样性、配图率、去重、AI 腔），
   润色与一致性校对由模型本身完成，不调外部 API
3. 重新生成 HTML，提醒 8:30 前审核后发布到公众号

心跳配置随仓库保存：`docs/automation-heartbeat.toml`。安装或更新到本地：

    cp ~/.codex/automations/ai/automation.toml ~/.codex/automations/ai/automation.toml.bak-$(date +%Y%m%d)
    cp docs/automation-heartbeat.toml ~/.codex/automations/ai/automation.toml

`~/.codex/` 对沙箱只读，替换后需重启 Codex 才会加载新配置。

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
