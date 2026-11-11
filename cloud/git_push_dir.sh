#!/usr/bin/env bash
# 用法：git_push_dir.sh <資料夾> <分支>
# 把資料夾內容以「沒有歷史」的單一 commit 強制推到指定分支，避免 repo 越來越大
set -euo pipefail
src="$1"; branch="$2"
tmp="$(mktemp -d)"
cp -a "$src"/. "$tmp"/
cd "$tmp"
git init -q
git checkout -q -b "$branch"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
git commit -qm "update $(TZ=Asia/Taipei date '+%Y-%m-%d %H:%M')" || exit 0
git push -qf "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "$branch"
echo "已推送 $branch"
