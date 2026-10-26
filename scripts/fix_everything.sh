#!/usr/bin/env bash
# 一次性把流水线从停摆状态救回来。
#
# 为什么要这个脚本：日报流水线依赖三样东西，全都在 Codex 沙箱的能力范围之外——
#   1) 联网（推代码、触发 Actions）
#   2) 写 ~/.codex/automations/ai/automation.toml（换掉仍在调用不存在的
#      request_permissions 的旧心跳配置）
#   3) 写 ~/.zshrc（第 8 行 source ~/.zshrc 自引用递归，导致 too many open files）
# 沙箱里这三件都做不了（Operation not permitted / 禁网），所以只能由你在
# 普通终端里跑一次。本脚本幂等：已经做过的步骤会自动跳过。
#
# 用法：
#   ./scripts/fix_everything.sh              # 实际执行
#   ./scripts/fix_everything.sh --dry-run    # 只报告
set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HEARTBEAT_SRC="${REPO}/docs/automation-heartbeat.toml"
HEARTBEAT_DST="${HOME}/.codex/automations/ai/automation.toml"
REPO_NAME="${REPO_NAME:-shirleybbbin-bot/ai-daily-news}"
TODAY="$(date +%F)"

# 中文标点紧邻变量时必须写 ${VAR}，否则 bash 3.2 会把多字节首字节并进变量名
run() {
  if [ "${DRY_RUN}" = 1 ]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

step() { echo ""; echo "━━━ $* ━━━"; }

cd "${REPO}"
echo "→ 仓库：${REPO}"

step "1/4 推送本地提交并取回云端产物"
# 每一步都尽力而为：没网时第 1、2 步会失败，但第 3、4 步不需要网络，
# 不能因为前面失败就把后面的修复一起带走（set -e 会直接退出）
if [ -x scripts/sync_and_push.sh ]; then
  if run ./scripts/sync_and_push.sh; then :; else
    echo "⚠️ 同步失败（多半是没网或 rebase 冲突），继续做后面不需要网络的步骤"
  fi
else
  echo "⚠️ 找不到 scripts/sync_and_push.sh，退回手动流程："
  echo "   git fetch origin && git rebase origin/main && git push origin HEAD:main"
  run git push origin HEAD:main || echo "⚠️ push 失败，继续后面的步骤"
fi

step "2/4 触发云端流水线"
if command -v gh >/dev/null 2>&1; then
  if gh auth status >/dev/null 2>&1; then
    run gh workflow run daily.yml --repo "${REPO_NAME}" || echo "⚠️ 触发失败，可去仓库 Actions 页面手动 Run workflow"
    echo "  查看运行：gh run list --repo ${REPO_NAME} --workflow daily.yml --limit 3"
    echo "  看失败日志：gh run view <run_id> --repo ${REPO_NAME} --log-failed"
  else
    echo "✋ gh 未登录，先执行：gh auth login -h github.com"
    echo "   登录后重跑本脚本，或手动：gh workflow run daily.yml --repo ${REPO_NAME}"
  fi
else
  echo "✋ 没装 gh CLI。去仓库页面 Actions → AI Daily News → Run workflow 手动触发。"
fi

step "3/4 替换本地心跳配置"
if [ ! -f "${HEARTBEAT_SRC}" ]; then
  echo "✋ 找不到 ${HEARTBEAT_SRC}，跳过"
elif [ -f "${HEARTBEAT_DST}" ] && grep -q 'qc_daily.py' "${HEARTBEAT_DST}" 2>/dev/null; then
  echo "  已是新版（含 qc_daily.py），跳过"
else
  if [ -f "${HEARTBEAT_DST}" ]; then
    run cp "${HEARTBEAT_DST}" "${HEARTBEAT_DST}.bak-$(date +%Y%m%d)"
    echo "  旧配置已备份到 ${HEARTBEAT_DST}.bak-$(date +%Y%m%d)"
  fi
  run cp "${HEARTBEAT_SRC}" "${HEARTBEAT_DST}"
  echo "  ✅ 已安装新心跳配置（重启 Codex 后生效）"
fi

step "4/4 修 ~/.zshrc 自引用递归"
ZSHRC="${HOME}/.zshrc"
if [ ! -f "${ZSHRC}" ]; then
  echo "  没有 ${ZSHRC}，跳过"
elif grep -qE '^[[:space:]]*source ~/\.zshrc[[:space:]]*$' "${ZSHRC}"; then
  run cp "${ZSHRC}" "${ZSHRC}.bak-$(date +%Y%m%d)"
  # 只注释掉整行自引用，不动其他内容
  run sed -i '' -E 's|^[[:space:]]*source ~/\.zshrc[[:space:]]*$|# source ~/.zshrc  # 自引用递归，已注释|' "${ZSHRC}"
  echo "  ✅ 已注释掉 source ~/.zshrc（原文件备份为 ${ZSHRC}.bak-$(date +%Y%m%d)）"
else
  echo "  已经没有自引用行，跳过"
fi

step "完成"
cat <<SUMMARY
接下来：
  1. 重启 Codex，让新的心跳配置生效
  2. 等云端跑完（约 10-20 分钟），再执行一次同步把产物取回本地：
       cd ${REPO} && ./scripts/sync_and_push.sh
  3. 离线质检当天的稿子：
       ./.venv/bin/python qc_daily.py data/today_data.json --date ${TODAY}
     退出码 0 才可交付；1 表示有 ❌ 硬伤，按报告逐条改 data/today_data.json 后
     用 ./.venv/bin/python gen_daily.py data/today_data.json --date ${TODAY} 重出 HTML
  4. 8:30 前审核后发布到公众号「AI洞察局」，并转企业微信「大装置」群与小助手各群
SUMMARY
