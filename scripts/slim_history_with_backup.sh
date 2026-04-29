#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_BRANCH="backup_pre_slim-${STAMP}"
BACKUP_TAG="backup-pre-slim-${STAMP}"
BACKUP_DIR="$REPO_DIR/dist/history-backup"
BUNDLE_PATH="$BACKUP_DIR/repo-pre-slim-${STAMP}.bundle"

mkdir -p "$BACKUP_DIR"

echo "[1/5] 记录瘦身前状态"
PRE_HEAD="$(git rev-parse --short HEAD)"
PRE_SIZE="$(git count-objects -vH | awk -F': ' '/size:/ {print $2; exit}')"

echo "[2/5] 创建可回滚备份（branch/tag/bundle）"
git branch "$BACKUP_BRANCH"
git tag "$BACKUP_TAG"
git bundle create "$BUNDLE_PATH" --all

echo "[3/5] 执行历史重写（移除大数据路径历史）"
git filter-repo --force \
  --path fullDataExtractionForBTC/data \
  --path dist/dataset \
  --invert-paths

echo "[4/5] GC 压缩对象"
git reflog expire --expire=now --all
git gc --prune=now --aggressive

echo "[5/5] 输出结果与回滚指引"
POST_HEAD="$(git rev-parse --short HEAD)"
POST_SIZE="$(git count-objects -vH | awk -F': ' '/size-pack:/ {print $2; exit}')"

echo "---"
echo "PRE_HEAD: $PRE_HEAD"
echo "POST_HEAD: $POST_HEAD"
echo "PRE_SIZE: $PRE_SIZE"
echo "POST_PACK_SIZE: $POST_SIZE"
echo "BACKUP_BRANCH: $BACKUP_BRANCH"
echo "BACKUP_TAG: $BACKUP_TAG"
echo "BUNDLE_PATH: $BUNDLE_PATH"
echo "---"
echo "回滚方式A（本仓库直接回滚）:"
echo "  git reset --hard $BACKUP_TAG"
echo "回滚方式B（从bundle恢复到新目录）:"
echo "  git clone \"$BUNDLE_PATH\" /tmp/AlphaPulse-restore"
