#!/usr/bin/env bash
# 构建 Jellyfin Downloader 插件 -> ./out
set -euo pipefail

export DOTNET_ROOT="${DOTNET_ROOT:-/opt/homebrew/opt/dotnet@9/libexec}"
export PATH="/opt/homebrew/opt/dotnet@9/bin:${PATH}"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_NOLOGO=1

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "${ROOT}/Jellyfin.Plugin.JellyfinDownloader"
dotnet publish -c Release -o "${ROOT}/out"
echo "built -> ${ROOT}/out"
