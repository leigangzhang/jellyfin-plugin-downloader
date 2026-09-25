#!/usr/bin/env bash
# 安装插件到 Jellyfin 并重启（kill -TERM + open -n）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
JF="${HOME}/Library/Application Support/jellyfin"
DEST="${JF}/plugins/JellyfinDownloader_1.0.0.0"

"${ROOT}/build.sh"

mkdir -p "${DEST}"
cp "${ROOT}/out/Jellyfin.Plugin.JellyfinDownloader.dll" "${DEST}/"
cp "${ROOT}/meta.json" "${DEST}/meta.json"
cp "${ROOT}/icon.png" "${DEST}/icon.png"

# 自带独立后端（已与 skill 剥离）；保留运行态 backend/state（历史快照/标记）
if [ -d "${DEST}/backend/state" ]; then
  rm -rf "${DEST}/backend-state.keep"
  mv "${DEST}/backend/state" "${DEST}/backend-state.keep"
fi
rm -rf "${DEST}/backend"
cp -R "${ROOT}/backend" "${DEST}/backend"
find "${DEST}/backend" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
if [ -d "${DEST}/backend-state.keep" ]; then
  rm -rf "${DEST}/backend/state"
  mv "${DEST}/backend-state.keep" "${DEST}/backend/state"
fi
mkdir -p "${DEST}/backend/state/snapshots" "${DEST}/backend/state/marks"

echo "installed -> ${DEST}"

# 重启 Jellyfin：必须停掉「所有」实例。
#   1) 只杀端口持有者（lsof -ti :8096）会漏掉绑定失败后空转的实例，多次重启会累积；
#   2) app 包装进程名是大写 `Jellyfin Server`，真服务端是小写 `jellyfin --webdir …`，
#      只按其中一个名字杀也会漏。
# 这里按可执行文件路径统一匹配，两个都覆盖。
if pgrep -f "/Applications/Jellyfin.app/Contents/MacOS" >/dev/null 2>&1; then
  pkill -f "/Applications/Jellyfin.app/Contents/MacOS" 2>/dev/null || true
  sleep 5
  if pgrep -f "/Applications/Jellyfin.app/Contents/MacOS" >/dev/null 2>&1; then
    pgrep -f "/Applications/Jellyfin.app/Contents/MacOS" | xargs -n 20 kill -9 2>/dev/null || true
    sleep 2
  fi
fi
open -n /Applications/Jellyfin.app
echo "jellyfin restarted"
