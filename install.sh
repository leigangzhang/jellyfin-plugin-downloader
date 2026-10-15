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

# ---------------------------------------------------------------------------
# 下载工具：安装期确保 aria2c / ffprobe / ffmpeg 在本机上，并把**绝对路径**记进
# <插件安装目录>/backend-tools.json。
#
# 为什么必须在安装期做：Jellyfin 从 Finder / `open` 启动时，插件后端继承的是
# launchd 的最小 PATH（没有 /opt/homebrew/bin），运行期按裸名找不到这三个工具，
# 于是探测失败被上报成「这个种子里没有可读视频」，环境故障伪装成资源问题。
#
# 记录文件由运行期的 Jellyfin.Plugin.JellyfinDownloader/ToolLocator.cs 读取：
# 下面的 **工具名、文件名、JSON 结构**与它逐字对应，改一边必须改另一边
# （tests/backend_probe/test_tool_paths.py 盯着这条约定）。
TOOL_NAMES="aria2c ffprobe ffmpeg"
TOOL_RECORD_NAME="backend-tools.json"
# PATH 之外再探这几个前缀：与 ToolLocator.CommonPrefixes 一致。
# JMD_TOOL_PREFIXES 是给守卫测试用的：空值 = 装作这台机器没有 Homebrew，
# 让「brew 不可用就非 0 退出」这条可确定地变红。
TOOL_PREFIXES="${JMD_TOOL_PREFIXES:-/opt/homebrew/bin /usr/local/bin /opt/local/bin}"

tool_install_hint() {
  case "$1" in
    aria2c) printf '%s' 'brew install aria2' ;;
    *) printf '%s' 'brew install ffmpeg' ;;
  esac
}

tool_brew_package() {
  case "$1" in
    aria2c) printf '%s' 'aria2' ;;
    *) printf '%s' 'ffmpeg' ;;
  esac
}

# 解析顺序与 ToolLocator.ResolveExecutable 一致：PATH → 常见前缀。
resolve_tool() {
  local name="$1" candidate prefix
  candidate="$(command -v "$name" 2>/dev/null || true)"
  if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then
    printf '%s\n' "${candidate}"
    return 0
  fi
  for prefix in ${TOOL_PREFIXES}; do
    if [ -x "${prefix}/${name}" ]; then
      printf '%s\n' "${prefix}/${name}"
      return 0
    fi
  done
  return 1
}

resolve_brew() {
  local candidate prefix
  candidate="$(command -v brew 2>/dev/null || true)"
  if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then
    printf '%s\n' "${candidate}"
    return 0
  fi
  for prefix in ${TOOL_PREFIXES}; do
    if [ -x "${prefix}/brew" ]; then
      printf '%s\n' "${prefix}/brew"
      return 0
    fi
  done
  return 1
}

# 缺什么装什么（brew 只按包名装一次：ffprobe 与 ffmpeg 同属 ffmpeg 包）。
# brew 不可用 → 明确报错 + 非 0 退出，不静默继续。
ensure_tools() {
  local name missing="" still="" packages="" package brew_bin
  for name in ${TOOL_NAMES}; do
    if ! resolve_tool "${name}" >/dev/null; then
      missing="${missing} ${name}"
    fi
  done

  if [ -z "${missing}" ]; then
    echo "下载工具：aria2c / ffprobe / ffmpeg 均已存在，跳过安装"
    return 0
  fi

  echo "下载工具缺失：${missing# }"
  if ! brew_bin="$(resolve_brew)"; then
    echo "错误：本机没有 Homebrew，无法自动安装缺失的下载工具。" >&2
    for name in ${missing}; do
      echo "  - ${name}：安装 Homebrew（https://brew.sh）后重跑本脚本，或用其它方式装上它（$(tool_install_hint "${name}")）" >&2
    done
    echo "安装未完成：下载工具不齐会让插件后端把环境故障误报成「种子里没有可读视频」。" >&2
    return 1
  fi

  for name in ${missing}; do
    package="$(tool_brew_package "${name}")"
    case " ${packages} " in
      *" ${package} "*) ;;
      *) packages="${packages} ${package}" ;;
    esac
  done

  for package in ${packages}; do
    echo "  安装 ${package}：brew install ${package}"
    "${brew_bin}" install "${package}" || {
      echo "错误：brew install ${package} 失败" >&2
      return 1
    }
  done

  # brew 刚装完，PATH 缓存里可能还没有新命令
  hash -r 2>/dev/null || true

  for name in ${TOOL_NAMES}; do
    if ! resolve_tool "${name}" >/dev/null; then
      still="${still} ${name}"
    fi
  done
  if [ -n "${still}" ]; then
    echo "错误：安装后仍找不到这些工具：${still# }" >&2
    for name in ${still}; do
      echo "  - ${name}：$(tool_install_hint "${name}")" >&2
    done
    return 1
  fi

  echo "下载工具安装完成：${missing# }"
  return 0
}

# 把三个工具的绝对路径写进 <目标目录>/backend-tools.json。
write_tool_record() {
  local dest="$1" name path tmp first=1
  mkdir -p "${dest}"
  tmp="$(mktemp)"
  {
    printf '{\n'
    printf '  "version": 1,\n'
    printf '  "written_by": "install.sh",\n'
    printf '  "written_at": "%s",\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf '  "tools": {'
    for name in ${TOOL_NAMES}; do
      path="$(resolve_tool "${name}")" || {
        rm -f "${tmp}"
        echo "错误：解析不到 ${name}，无法写记录文件" >&2
        return 1
      }
      case "${path}" in
        *'"'* | *'\'*)
          rm -f "${tmp}"
          echo "错误：${name} 的路径含引号或反斜杠，无法写进 JSON：${path}" >&2
          return 1
          ;;
      esac
      if [ "${first}" -eq 0 ]; then
        printf ','
      fi
      printf '\n    "%s": "%s"' "${name}" "${path}"
      first=0
    done
    printf '\n  }\n}\n'
  } > "${tmp}"
  mv "${tmp}" "${dest}/${TOOL_RECORD_NAME}"
  echo "已记录下载工具路径 -> ${dest}/${TOOL_RECORD_NAME}"
}

# 只写工具记录就退出：给 tests/backend_probe/test_tool_paths.py 用。
# **必须放在 build.sh 与 pkill Jellyfin 之前**：这条分支不构建、不碰 Jellyfin，
# 只往给定目录写一个 JSON。放在后面会让一个纯格式测试变成一次真实安装。
if [ "${1:-}" = "--record-tools-only" ]; then
  DEST_DIR="${2:-}"
  if [ -z "${DEST_DIR}" ]; then
    echo "用法：install.sh --record-tools-only <目标目录>" >&2
    exit 2
  fi
  ensure_tools
  write_tool_record "${DEST_DIR}"
  exit 0
fi

"${ROOT}/build.sh"

# 工具齐了再往下走：先把「缺工具」拦在这里，此时 Jellyfin 还没被停，
# 报错退出不会留下一个停着的 Jellyfin。
ensure_tools

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
  mkdir -p "${DEST}/backend/state/snapshots"

  # 记录三个下载工具的绝对路径：运行期 ToolLocator.cs 读这一份，
  # 后端不再依赖 PATH（launchd 最小 PATH 里没有 /opt/homebrew/bin）。
  write_tool_record "${DEST}"

  echo "installed -> ${DEST}"
done <<< "${DIRS}"

# 重启 Jellyfin（停止逻辑已移到最前面，这里只拉起一个干净实例）。
open -n /Applications/Jellyfin.app
echo "jellyfin restarted"
