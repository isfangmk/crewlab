#!/bin/sh
# 构建沙盒执行镜像（在 WSL 内执行）。沙盒运行时断网，依赖固化在镜像里。
# 用法: sh scripts/build-sandbox-image.sh [镜像tag]
# 默认 tag: ai-workbench-sandbox:local
set -eu

TAG="${1:-ai-workbench-sandbox:local}"
# 脚本所在目录 → backend/sandbox（Dockerfile + requirements.txt 所在处）
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
CONTEXT="$ROOT/backend/sandbox"

if [ ! -f "$CONTEXT/Dockerfile" ]; then
  echo "ERROR 找不到 Dockerfile: $CONTEXT/Dockerfile" >&2
  exit 1
fi

echo "构建沙盒镜像 tag=$TAG context=$CONTEXT"
docker build -t "$TAG" "$CONTEXT"
echo "完成: $TAG"
echo "下一步: 把 backend/.env 的 SANDBOX_IMAGE 改为 $TAG，然后重启后端"
