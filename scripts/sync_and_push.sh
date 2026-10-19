#!/usr/bin/env bash
# 本地 ↔ 云端安全同步。
#
# 背景：GitHub Actions 每天把 data/today_data.json 与 data/ai-daily-*.html 提交回 main，
# 本地也会生成同名文件。直接 git pull 会因为「未跟踪文件会被覆盖」而中止，
# 直接 git push 又会因为云端已前进而被拒。这个脚本按固定顺序处理：
#   拦住未提交改动 → fetch → 备份会撞车的本地产物 → rebase → push
#
# 用法：
#   ./scripts/sync_and_push.sh              # 实际执行
#   ./scripts/sync_and_push.sh --dry-run    # 只报告会发生什么，不改任何东西
#
# 注意：字符串里凡是被中文标点紧挨着的变量都写成 ${VAR}，
# 否则 bash 3.2 会把多字节字符的首字节并进变量名，报 unbound variable。
set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"

cd "$(cd "$(dirname "$0")/.." && pwd)"
echo "→ 仓库：$(pwd)"

run() {
  if [ "${DRY_RUN}" = 1 ]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "${CURRENT_BRANCH}" != "${BRANCH}" ]; then
  echo "✋ 当前分支是 ${CURRENT_BRANCH}，不是 ${BRANCH}。切过去再跑，或用 BRANCH=... 覆盖。"
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "✋ 有未提交的改动，rebase 会搞乱工作区。先 commit 或 stash："
  git status --short
  exit 1
fi

# fetch 只更新远端跟踪引用，不动工作区，dry-run 也要真跑，否则判断全是过期的
echo "→ git fetch ${REMOTE} ${BRANCH}"
git fetch --quiet "${REMOTE}" "${BRANCH}"

AHEAD="$(git rev-list --count "${REMOTE}/${BRANCH}..HEAD")"
BEHIND="$(git rev-list --count "HEAD..${REMOTE}/${BRANCH}")"
echo "  本地领先 ${AHEAD} 个提交，落后 ${BEHIND} 个提交"

if [ "${BEHIND}" -gt 0 ]; then
  echo "→ 云端比本地多的改动："
  git log --oneline "HEAD..${REMOTE}/${BRANCH}" | sed 's/^/    /'
fi

# 云端已跟踪、但本地是未跟踪状态的同名文件，会让 rebase 直接中止 → 先挪进备份目录
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="data/.local-backup/${TS}"
COLLIDE="$(comm -12 \
  <(git ls-tree -r --name-only "${REMOTE}/${BRANCH}" -- data | LC_ALL=C sort) \
  <(git ls-files --others --exclude-standard -- data | LC_ALL=C sort))"

if [ -n "${COLLIDE}" ]; then
  echo "→ 本地未跟踪产物与云端撞车，备份到 ${BACKUP_DIR}："
  echo "${COLLIDE}" | sed 's/^/    /'
  if [ "${DRY_RUN}" = 0 ]; then
    mkdir -p "${BACKUP_DIR}"
    echo "${COLLIDE}" | while IFS= read -r f; do
      [ -n "${f}" ] || continue
      mkdir -p "${BACKUP_DIR}/$(dirname "${f}")"
      mv "${f}" "${BACKUP_DIR}/${f}"
    done
  fi
else
  echo "  没有会撞车的本地未跟踪产物"
fi

if [ "${BEHIND}" -gt 0 ]; then
  echo "→ git rebase ${REMOTE}/${BRANCH}"
  if ! run git rebase "${REMOTE}/${BRANCH}"; then
    echo "❌ rebase 冲突。手动解决后执行 git rebase --continue，再重跑本脚本。"
    echo "   想放弃这次 rebase：git rebase --abort"
    exit 1
  fi
  if [ "${DRY_RUN}" = 0 ]; then
    AHEAD="$(git rev-list --count "${REMOTE}/${BRANCH}..HEAD")"
  fi
fi

if [ "${AHEAD}" -gt 0 ]; then
  echo "→ git push ${REMOTE} HEAD:${BRANCH}（${AHEAD} 个提交）"
  git log --oneline "${REMOTE}/${BRANCH}..HEAD" | sed 's/^/    /'
  run git push "${REMOTE}" "HEAD:${BRANCH}"
else
  echo "  本地没有待推送的提交"
fi

if [ "${DRY_RUN}" = 1 ]; then
  echo "✅ dry-run 结束，除了 fetch 没有改动任何东西"
  exit 0
fi

echo "→ 同步完成"
echo "  今天的日报：data/ai-daily-$(date +%F).html"
git log --oneline -3 | sed 's/^/  /'
