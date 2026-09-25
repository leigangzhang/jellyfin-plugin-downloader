#!/usr/bin/env bash
# 干净卸载 Jellyfin Downloader 插件（**不停 Jellyfin、不重启**）
#
# 为什么不停服：Jellyfin 后台的「卸载」按钮本身就只删文件、不碰运行中的进程。
# 本脚本沿用同样的思路——只清理插件自己的东西，Jellyfin 全程继续正常提供服务。
# 代价是运行中的实例仍持有已加载的程序集（详情页注入脚本、/JellyfinDownloader/* 代理端点），
# 这部分要等你下次自然重启 Jellyfin 才会消失；脚本只如实报告，不代替你重启。
#
# 做的事：
#   留档改前内容 → 删插件目录 → 删配置/日志 → 摘本地仓库条目（仅 Jellyfin 未运行时）
#   → 回收插件后端进程 → 文件层面自检 + 运行态报告
#
# 用法：
#   ./uninstall.sh                    # 卸载（默认：不停服、不重启、零影响）
#   ./uninstall.sh --verify-only      # 只自检，不改任何东西；重启 Jellyfin 后再跑一次可复核运行态
#   ./uninstall.sh --dry-run          # 只打印将要做什么
#   ./uninstall.sh --keep-backend     # 保留插件后端进程（默认回收，它不属于 Jellyfin 本体）
#   ./uninstall.sh --clean-repo-entry # 即便 Jellyfin 在运行也改写 system.xml（不推荐，见下）
#   ./uninstall.sh --purge-data       # 插件运行数据不留档
#   ./uninstall.sh --purge-legacy     # 连 skill 侧旧数据一起删（先留档）
#   ./uninstall.sh --no-backup        # 不留档（不推荐）
#   ./uninstall.sh -h                 # 看这份说明
#
# 兼容：--no-stop / --no-restart 保留为无副作用的空开关（默认行为已经是不停服、不重启）
#
# 关于 system.xml 里的「Jellyfin Downloader (local)」仓库条目：
#   运行中的 Jellyfin 把配置放在内存里，任何一次配置改动都会把内存副本整体写回 system.xml。
#   因此在 Jellyfin 运行时改文件会被覆盖回去，所以本脚本默认**不动这个文件**，改为提示你：
#     控制台 → 插件 → 仓库 → 删掉「Jellyfin Downloader (local)」（无需重启，立即生效）
#   或者等 Jellyfin 停着的时候跑 `./uninstall.sh --clean-repo-entry` 让脚本代劳。
#
# 测试钩子：JMD_JELLYFIN_ROOT / JMD_LEGACY_ROOT / JMD_BACKUP_ROOT 可把三处根目录指向别处
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
JF="${JMD_JELLYFIN_ROOT:-$HOME/Library/Application Support/jellyfin}"
PLUGIN_DIR_NAME="JellyfinDownloader_1.0.0.0"            # 源码 install.sh 装出来的目录名
PLUGIN_DIR_NAME_CATALOG="Jellyfin Downloader_1.0.0.0"    # Jellyfin 插件仓库装出来的目录名（取显示名，带空格）
PLUGIN_GUID="b7f3e2a1-6c4d-4e9b-9a21-7d5c8e1f0a33"
PLUGIN_VERSION="1.0.0.0"
CONF_DIR="${JF}/plugins/configurations"
CONF="${CONF_DIR}/Jellyfin.Plugin.JellyfinDownloader.xml"
BACKEND_LOG="${CONF_DIR}/jellyfin-downloader-backend.log"
SYS="${JF}/config/system.xml"
LEGACY="${JMD_LEGACY_ROOT:-${HOME}/Library/Application Support/JellyfinDownloader}"
BACKUP_ROOT="${JMD_BACKUP_ROOT:-${ROOT}/backups}"
REPO_NAME="Jellyfin Downloader (local)"
BASE_URL="http://127.0.0.1:8096"
BACKEND_PORT=8123

DRY_RUN=0; VERIFY_ONLY=0; PURGE_DATA=0; PURGE_LEGACY=0; NO_BACKUP=0; KEEP_BACKEND=0; FORCE_REPO=0
SKIP_HTTP="${JMD_SKIP_HTTP:-0}"   # 夹具/离线演练：跳过依赖 8096 的检查

usage() {
  local end
  end="$(grep -n '^set -euo pipefail$' "$0" | head -1 | cut -d: -f1)"
  end=$(( end > 2 ? end - 1 : 30 ))
  sed -n "2,${end}p" "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)          DRY_RUN=1 ;;
    --verify-only)      VERIFY_ONLY=1 ;;
    --purge-data)       PURGE_DATA=1 ;;
    --purge-legacy)     PURGE_LEGACY=1 ;;
    --no-backup)        NO_BACKUP=1 ;;
    --keep-backend)     KEEP_BACKEND=1 ;;
    --clean-repo-entry) FORCE_REPO=1 ;;
    --no-stop|--no-restart) : ;;   # 兼容旧用法：现在默认就不停服、不重启
    -h|--help)          usage 0 ;;
    *) echo "未知参数：$1（-h 看用法）" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[error]\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [ "$DRY_RUN" = 1 ]; then printf '    [dry-run] %s\n' "$*"; else "$@"; fi; }

TS="$(date +%Y%m%d-%H%M%S)"
SNAP="${BACKUP_ROOT}/jellyfin-downloader-uninstall-${TS}"

[ -d "$JF" ] || die "Jellyfin 数据根不存在：${JF}"

jellyfin_running() { pgrep -f "/Applications/Jellyfin.app/Contents/MacOS" >/dev/null 2>&1; }

# 已安装的插件目录（两种来源都认）：以 meta.json 里的 guid 为准，目录名变了也能找到；
# meta.json 缺失/损坏时回退到两个已知目录名。
plugin_dirs() {
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
http() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null || echo 000; }
body() { curl -s --max-time 8 "$1" 2>/dev/null || true; }

# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
PASS=0; FAIL=0; SOFT=0
ok()   { printf '  \033[32m✅\033[0m %-40s %s\n' "$1" "$2"; PASS=$((PASS + 1)); }
bad()  { printf '  \033[31m❌\033[0m %-40s 期望 %s，实际 %s\n' "$1" "$2" "$3"; FAIL=$((FAIL + 1)); }
note() { printf '  \033[33m•\033[0m  %-40s %s\n' "$1" "$2"; SOFT=$((SOFT + 1)); }
check() { if [ "$2" = "$3" ]; then ok "$1" "$3"; else bad "$1" "$2" "$3"; fi; }

do_checks() { # $1 = normal | verify
  local mode="$1"
  local conf_left inj resid script_code style_code manifest_code dirs
  conf_left="$(ls "$CONF_DIR" 2>/dev/null | grep -ci 'jellyfindownloader\|jellyfin-downloader' || true)"
  resid="$(grep -ril --exclude-dir=log --exclude='*.db*' --exclude='system.xml' \
            "jellyfindownloader\|${PLUGIN_GUID}" "$JF" 2>/dev/null | wc -l | tr -d ' ' || true)"
  script_code=skip; style_code=skip; manifest_code=skip; inj=skip
  if [ "$SKIP_HTTP" != 1 ]; then
    script_code="$(http "${BASE_URL}/JellyfinDownloader/script")"
    style_code="$(http "${BASE_URL}/JellyfinDownloader/style")"
    manifest_code="$(http "${BASE_URL}/JellyfinDownloader/manifest.json")"
    inj="$(body "${BASE_URL}/web/index.html" | grep -c 'JellyfinDownloader' || true)"
  fi

  dirs="$(plugin_dirs)"
  say "自检"
  # --- 文件层面：卸载动作是否真落盘（任何时候都必须过）---
  check "插件目录已删除"                          "absent" "$([ -z "$dirs" ] && echo absent || echo "present: $(printf '%s' "$dirs" | tr '\n' ' ')")"
  check "插件配置/后端日志残留数"                  "0"      "${conf_left:-0}"
  if [ "$SKIP_HTTP" = 1 ]; then
    note "HTTP 检查（图标/注入）" "已按 JMD_SKIP_HTTP=1 跳过"
  else
  check "插件图标端点 /Plugins/<guid>/Image"       "404"    "$(http "${BASE_URL}/Plugins/${PLUGIN_GUID}/${PLUGIN_VERSION}/Image")"
  fi
  check "文件残留引用（排除日志/db/system.xml）"    "0"      "${resid:-0}"
  if [ "$KEEP_BACKEND" = 1 ]; then
    note "插件后端进程" "按 --keep-backend 保留"
  else
    check "插件后端进程"                           "none"   "$(pgrep -f console_server.py >/dev/null 2>&1 && echo running || echo none)"
    check "端口 ${BACKEND_PORT}"                   "free"   "$([ -x /usr/sbin/lsof ] && /usr/sbin/lsof -nP -iTCP:${BACKEND_PORT} -sTCP:LISTEN >/dev/null 2>&1 && echo occupied || echo free)"
  fi

  # --- 本地仓库条目 ---
  if [ -f "$SYS" ] && grep -qi 'jellyfindownloader' "$SYS"; then
    note "本地插件仓库条目" "仍在 system.xml（处理办法见上一步）"
  else
    ok "本地插件仓库条目已清除" "clean"
  fi

  # --- 运行态：程序集是否还在内存里（不重启就不该指望它消失）---
  if [ "$SKIP_HTTP" = 1 ]; then
    note "运行态检查" "已按 JMD_SKIP_HTTP=1 跳过"
  elif [ "$mode" = "verify" ]; then
    check "插件端点 /JellyfinDownloader/script"        "404" "$script_code"
    check "插件端点 /JellyfinDownloader/style"         "404" "$style_code"
    check "插件端点 /JellyfinDownloader/manifest.json" "404" "$manifest_code"
    check "首页注入计数（/web/index.html）"             "0"   "${inj:-0}"
  elif [ "$script_code" = "404" ] && [ "${inj:-0}" = "0" ]; then
    ok "运行态已干净（未加载插件）" "clean"
  else
    note "运行态仍有插件（script=${script_code} / 注入 ${inj:-0} 处）" "下次重启 Jellyfin 后消失"
  fi
}

# ---------------------------------------------------------------------------
# 只自检
# ---------------------------------------------------------------------------
if [ "$VERIFY_ONLY" = 1 ]; then
  if jellyfin_running; then RUN_STATE="运行中"; else RUN_STATE="未运行"; fi
  say "只自检（不改动任何东西；Jellyfin ${RUN_STATE}）"
  do_checks verify
  echo
  if [ "$FAIL" -eq 0 ]; then say "全部检查通过"; exit 0; fi
  say "有 ${FAIL} 项未通过"
  exit 1
fi

# ---------------------------------------------------------------------------
# DRY-RUN
# ---------------------------------------------------------------------------
if [ "$DRY_RUN" = 1 ]; then
  say "DRY-RUN：只显示计划，不修改任何文件、不碰任何进程"
  if jellyfin_running; then JF_STATE="运行中（本脚本不会停它、也不会重启它）"; else JF_STATE="未运行"; fi
  info "Jellyfin        : ${JF_STATE}"
  if [ -n "$(plugin_dirs)" ]; then
    info "插件目录        : $(plugin_dirs | tr '\n' ' ')（存在，将删除）"
  else
    info "插件目录        : （不存在）"
  fi
  info "插件配置        : ${CONF} $([ -f "$CONF" ] && echo '(存在，将删除)' || echo '(不存在)')"
  info "配置备份/后端日志: ${CONF_DIR} 下 Jellyfin.Plugin.JellyfinDownloader.xml.bak-*、jellyfin-downloader-backend.log"
  if [ -f "$SYS" ] && grep -qi jellyfindownloader "$SYS"; then REPO_STATE="存在"; else REPO_STATE="不存在"; fi
  if [ "$FORCE_REPO" = 1 ]; then REPO_PLAN="（--clean-repo-entry：将改写 system.xml）"; else REPO_PLAN="（Jellyfin 运行时不改文件，只给处理办法）"; fi
  info "本地仓库条目    : ${REPO_STATE}${REPO_PLAN}"
  if [ "$NO_BACKUP" = 1 ]; then BK_PLAN="--no-backup：不留档"; else BK_PLAN="改前内容复制到 ${BACKUP_ROOT}/jellyfin-downloader-uninstall-<时间戳>/"; fi
  info "留档            : ${BK_PLAN}"
  if [ "$PURGE_DATA" = 1 ]; then DATA_PLAN="--purge-data：不留档"; else DATA_PLAN="默认随插件目录一起留档"; fi
  info "插件运行数据    : ${DATA_PLAN}"
  if [ "$KEEP_BACKEND" = 1 ]; then BE_PLAN="--keep-backend：保留"; else BE_PLAN="回收 8123 上的 console_server.py（不属于 Jellyfin 本体）"; fi
  info "插件后端进程    : ${BE_PLAN}"
  if [ "$PURGE_LEGACY" = 1 ]; then LG_PLAN="(将删除，先留档)"; else LG_PLAN="(保留，仅报告)"; fi
  info "skill 侧旧数据  : ${LEGACY} ${LG_PLAN}"
  info "Jellyfin 进程   : 不停、不重启，全程零影响"
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. 留档改前内容
# ---------------------------------------------------------------------------
RUNNING_BEFORE=0
if jellyfin_running; then RUNNING_BEFORE=1; fi

if [ "$NO_BACKUP" = 1 ]; then
  warn "--no-backup：本次不留档"
else
  say "留档改前内容（回滚用）"
  mkdir -p "${SNAP}/removed-config" "${SNAP}/pre-clean-config" "${SNAP}/plugin" "${SNAP}/legacy"
  SNAP_ITEMS=0
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    name="$(basename "$d")"
    if [ "$PURGE_DATA" = 1 ]; then
      run ditto --norsrc --noextattr "$d" "${SNAP}/plugin/${name}" 2>/dev/null \
        || run cp -R "$d" "${SNAP}/plugin/${name}"
      info "插件本体已留档（--purge-data：运行数据不留）：${name}"
    else
      run ditto "$d" "${SNAP}/plugin/${name}"
      info "插件本体 + 运行数据（backend/state）已留档：${name}"
    fi
    SNAP_ITEMS=$((SNAP_ITEMS + 1))
  done < <(plugin_dirs)
  for f in "$CONF" "$BACKEND_LOG" "${CONF}".bak-*; do
    if [ -f "$f" ]; then run cp -p "$f" "${SNAP}/removed-config/"; SNAP_ITEMS=$((SNAP_ITEMS + 1)); fi
  done
  if [ -f "$SYS" ] && grep -qi "jellyfindownloader" "$SYS"; then
    run cp -p "$SYS" "${SNAP}/pre-clean-config/"; SNAP_ITEMS=$((SNAP_ITEMS + 1))
  fi
  if [ "$PURGE_LEGACY" = 1 ] && [ -d "$LEGACY" ]; then
    run ditto "$LEGACY" "${SNAP}/legacy/JellyfinDownloader"
    info "skill 侧旧数据已留档"
    SNAP_ITEMS=$((SNAP_ITEMS + 1))
  fi
  if [ "$PURGE_DATA" = 1 ]; then
    while IFS= read -r d; do
      [ -n "$d" ] || continue
      run rm -rf "${SNAP}/plugin/$(basename "$d")/backend/state"
    done < <(plugin_dirs)
  fi
  if [ "$SNAP_ITEMS" -gt 0 ]; then
    ( cd "$SNAP" && find . -type f ! -name 'MANIFEST.sha256' -print0 | sort -z | xargs -0 shasum -a 256 > MANIFEST.sha256 ) || true
  else
    info "插件目录/配置/日志/旧数据都不存在，无需留档"
    rm -rf "$SNAP"
  fi
fi

# ---------------------------------------------------------------------------
# 2. 删插件目录（运行中的 Jellyfin 已把 DLL 映射进内存，删文件不影响它继续服务）
# ---------------------------------------------------------------------------
say "删除插件目录"
DELETED=0
while IFS= read -r d; do
  [ -n "$d" ] || continue
  run rm -rf "$d"
  info "已删除 ${d}"
  DELETED=1
done < <(plugin_dirs)
if [ "$DELETED" = 0 ]; then
  info "没有已安装的插件目录（Jellyfin 后台卸载按钮可能已删掉）"
fi
for d in "${JF}/plugins/${PLUGIN_DIR_NAME}".* "${JF}/plugins/.${PLUGIN_DIR_NAME}"* \
         "${JF}/plugins/${PLUGIN_DIR_NAME_CATALOG}".* "${JF}/plugins/.${PLUGIN_DIR_NAME_CATALOG}"*; do
  if [ -e "$d" ]; then warn "发现残留目录 $d"; run rm -rf "$d"; fi
done

# ---------------------------------------------------------------------------
# 3. 删插件配置与后端日志
# ---------------------------------------------------------------------------
say "删除插件配置与日志"
removed=0
for f in "$CONF" "$BACKEND_LOG" "${CONF}".bak-*; do
  if [ -f "$f" ]; then run rm -f "$f"; info "已删除 $(basename "$f")"; removed=1; fi
done
if [ "$removed" = 0 ]; then info "无残留配置/日志"; fi

# ---------------------------------------------------------------------------
# 4. 本地仓库条目
#    Jellyfin 运行时配置在内存里，改文件会被内存副本覆盖，故默认不改，只给办法
# ---------------------------------------------------------------------------
say "本地插件仓库条目"
if [ ! -f "$SYS" ]; then
  warn "找不到 ${SYS}，跳过"
elif ! grep -qi "jellyfindownloader" "$SYS"; then
  info "system.xml 中已无该条目"
elif [ "$RUNNING_BEFORE" = 1 ] && [ "$FORCE_REPO" = 0 ]; then
  warn "Jellyfin 正在运行：不改写 system.xml（改了也会被内存里的配置覆盖回去）"
  info "处理办法（二选一，都不需要重启 Jellyfin）："
  info "  a) 控制台 → 插件 → 仓库 → 删掉「${REPO_NAME}」（推荐，立即生效）"
  info "  b) 停掉 Jellyfin 后跑：./uninstall.sh --clean-repo-entry"
else
  python3 - "$SYS" <<'PY'
import os, re, sys, xml.etree.ElementTree as ET
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
blocks = re.findall(r"[ \t]*<RepositoryInfo>.*?</RepositoryInfo>\n?", s, re.S)
targets = [b for b in blocks if "JellyfinDownloader" in b]
if not targets:
    print("    XML 里没有匹配的 <RepositoryInfo> 块，未改动")
    sys.exit(0)
for b in targets:
    s = s.replace(b, "", 1)
ET.fromstring(s)  # 语法校验，坏了就不写
tmp = p + ".tmp-uninstall"
open(tmp, "w", encoding="utf-8").write(s)
os.replace(tmp, p)
print(f"    已摘除 {len(targets)} 条本地仓库条目，XML 语法校验通过")
PY
fi

# ---------------------------------------------------------------------------
# 5. 回收插件后端进程（它是插件拉起的 python，不属于 Jellyfin 本体）
# ---------------------------------------------------------------------------
say "插件后端进程"
if [ "$KEEP_BACKEND" = 1 ]; then
  info "按 --keep-backend 保留：$(pgrep -f console_server.py | tr '\n' ' ')"
elif pgrep -f "console_server.py" >/dev/null 2>&1; then
  info "回收 $(pgrep -f console_server.py | tr '\n' ' ')"
  pgrep -f "console_server.py" | xargs -n1 kill -TERM 2>/dev/null || true
  sleep 2
  if pgrep -f "console_server.py" >/dev/null 2>&1; then
    pgrep -f "console_server.py" | xargs -n 20 kill -9 2>/dev/null || true
    sleep 1
  fi
  info "已停止（Jellyfin 本体不受影响）"
else
  info "没有在跑的插件后端"
fi
if [ -x /usr/sbin/lsof ] && /usr/sbin/lsof -nP -iTCP:${BACKEND_PORT} -sTCP:LISTEN >/dev/null 2>&1; then
  if pgrep -f "console_server.py" >/dev/null 2>&1; then
    warn "端口 ${BACKEND_PORT} 仍被插件后端占用，手工确认：lsof -nP -iTCP:${BACKEND_PORT}"
  else
    warn "端口 ${BACKEND_PORT} 被别的进程占用，未动它：$(/usr/sbin/lsof -nP -iTCP:${BACKEND_PORT} -sTCP:LISTEN | tail -1)"
  fi
fi

# ---------------------------------------------------------------------------
# 6. skill 侧旧数据（不属于 Jellyfin 插件，默认只报告）
# ---------------------------------------------------------------------------
if [ -d "$LEGACY" ]; then
  if [ "$PURGE_LEGACY" = 1 ]; then
    say "删除 skill 侧旧数据"
    run rm -rf "$LEGACY"
    info "已删除 ${LEGACY}（快照里留有副本）"
  else
    info "skill 侧旧数据保留（不属 Jellyfin 插件）：${LEGACY}（要删加 --purge-legacy）"
  fi
fi

# ---------------------------------------------------------------------------
# 7. 自检
# ---------------------------------------------------------------------------
do_checks normal

echo
if [ "$FAIL" -eq 0 ]; then
  say "文件层面已卸载干净；Jellyfin 全程未被停服或重启"
  if [ "$NO_BACKUP" = 0 ] && [ -d "$SNAP" ]; then info "回滚快照：${SNAP}"; fi
  if [ "$SOFT" -gt 0 ]; then
    info "上面 • 项不是失败：运行中的实例还持有已加载的程序集，你下次重启 Jellyfin 后自动消失"
    info "重启之后再跑一次「./uninstall.sh --verify-only」即可复核运行态"
  fi
  exit 0
fi
say "有 ${FAIL} 项未通过，请按上面 ❌ 排查"
if [ "$NO_BACKUP" = 0 ] && [ -d "$SNAP" ]; then info "回滚快照：${SNAP}"; fi
exit 1
