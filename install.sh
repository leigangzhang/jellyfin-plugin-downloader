#!/usr/bin/env bash
# 构建并安装 Jellyfin Downloader 插件，然后重启 Jellyfin（kill -TERM + open -n）
#
# 安装目录会自适应：
#   · 源码这条路装出来的是 plugins/JellyfinDownloader_1.0.0.0
#   · 从 Jellyfin 插件仓库装出来的是 plugins/Jellyfin Downloader_1.0.0.0（Jellyfin 用插件显示名建目录）
# 脚本按 meta.json 里的 guid 找已存在的目录，装回同一个地方，避免同一插件出现两份。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
JF="${JMD_JELLYFIN_ROOT:-${HOME}/Library/Application Support/jellyfin}"
PLUGIN_GUID="b7f3e2a1-6c4d-4e9b-9a21-7d5c8e1f0a33"
PLUGIN_DIR_NAME="JellyfinDownloader_1.0.0.0"
PLUGIN_DIR_NAME_CATALOG="Jellyfin Downloader_1.0.0.0"

"${ROOT}/build.sh"

# 已安装目录：以 meta.json 的 guid 为准，找不到就用源码这条路的标准目录名
find_installed_dirs() {
  local d
  {
    for d in "${JF}/plugins"/*/; do
      [ -f "${d}meta.json" ] || continue
      if grep -q "${PLUGIN_GUID}" "${d}meta.json" 2>/dev/null; then printf '%s\n' "${d%/}"; fi
    done
    for d in "${JF}/plugins/${PLUGIN_DIR_NAME}" "${JF}/plugins/${PLUGIN_DIR_NAME_CATALOG}"; do
      if [ -d "$d" ]; then printf '%s\n' "$d"; fi
    done
  } | sort -u
}

# 先停掉 Jellyfin，再覆盖 DLL/后端：否则运行中的进程会在 copy 的瞬间
# 读到半写好的 DLL，触发 `System.BadImageFormatException: Bad IL range`。
if pgrep -f "/Applications/Jellyfin.app/Contents/MacOS" >/dev/null 2>&1; then
  pkill -f "/Applications/Jellyfin.app/Contents/MacOS" 2>/dev/null || true
  sleep 5
  if pgrep -f "/Applications/Jellyfin.app/Contents/MacOS" >/dev/null 2>&1; then
    pgrep -f "/Applications/Jellyfin.app/Contents/MacOS" | xargs -n 20 kill -9 2>/dev/null || true
    sleep 2
  fi
fi

DIRS="$(find_installed_dirs)"
COUNT="$(printf '%s' "$DIRS" | grep -c . || true)"
if [ "${COUNT:-0}" -gt 1 ]; then
  echo "warn: 检测到多个已安装目录，将逐一更新：" >&2
  printf '  %s\n' $DIRS >&2
fi
if [ "${COUNT:-0}" -eq 0 ]; then
  DEST="${JF}/plugins/${PLUGIN_DIR_NAME}"
  mkdir -p "${DEST}"
  DIRS="${DEST}"
  echo "未发现已安装目录，安装到 ${DEST}"
else
  echo "安装到已存在目录：$(printf '%s' "$DIRS" | tr '\n' ' ')"
fi

while IFS= read -r DEST; do
  [ -n "$DEST" ] || continue
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
done <<< "${DIRS}"

# 重启 Jellyfin（停止逻辑已移到最前面，这里只拉起一个干净实例）。
open -n /Applications/Jellyfin.app
echo "jellyfin restarted"
