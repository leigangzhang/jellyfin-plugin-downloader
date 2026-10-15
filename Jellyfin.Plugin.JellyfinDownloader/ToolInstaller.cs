using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>
/// 下载工具（aria2c / ffprobe / ffmpeg）缺失时的引导文案。
///
/// 与 <see cref="PythonInstaller"/> 同一形状与分工：**只给出可读的说明与命令**，
/// 不代跑安装（安装归 install.sh 与用户自己）。这里是那段文案的唯一出处——
/// 配置页、后端启动日志都取这一份，避免「缺什么、怎么装」写散成两份。
/// </summary>
public static class ToolInstaller
{
    /// <summary>
    /// 给出缺失项与安装命令；都不缺时 <c>Needed</c> 为 false。
    /// </summary>
    public static (bool Needed, string Detail, string Command, string Url, IReadOnlyList<string> Options) Guide(
        IReadOnlyList<ToolLocator.Resolution> resolutions)
    {
        var missing = (resolutions ?? Array.Empty<ToolLocator.Resolution>())
            .Where(item => !item.Ok)
            .ToList();

        if (missing.Count == 0)
        {
            return (false, "aria2c / ffprobe / ffmpeg 都已就绪。", string.Empty, string.Empty, Array.Empty<string>());
        }

        var names = string.Join("、", missing.Select(item => item.Name));

        if (OperatingSystem.IsMacOS())
        {
            var options = missing
                .Select(item => item.InstallHint)
                .Distinct(StringComparer.Ordinal)
                .ToList();
            return (
                true,
                "缺少下载工具：" + names + "。在终端执行下面的命令装上，然后回到本页点「重启后端」——"
                    + "这三个工具是插件后端探测种子/媒体的必需品，缺了会让「机器上没装工具」被误报成"
                    + "「这个种子里没有可读视频」。",
                string.Join(" && ", options),
                "https://brew.sh",
                options);
        }

        if (OperatingSystem.IsLinux())
        {
            var packages = LinuxPackages(missing);
            var command = File.Exists("/usr/bin/dnf") || File.Exists("/usr/bin/yum")
                ? "sudo dnf install -y " + packages
                : "sudo apt-get install -y " + packages;
            return (
                true,
                "缺少下载工具：" + names + "。Linux 上装它们需要包管理器权限，本插件不代跑 sudo。"
                    + "请在终端执行下面的命令，然后回到本页点「重启后端」。",
                command,
                "https://ffmpeg.org/download.html",
                new List<string> { command });
        }

        var winget = new List<string>();
        if (missing.Any(item => string.Equals(item.Name, "aria2c", StringComparison.Ordinal)))
        {
            winget.Add("winget install aria2.aria2");
        }

        if (missing.Any(item => !string.Equals(item.Name, "aria2c", StringComparison.Ordinal)))
        {
            winget.Add("winget install Gyan.FFmpeg");
        }

        return (
            true,
            "缺少下载工具：" + names + "。Windows 上请用 winget 安装，然后回到本页点「重启后端」。",
            string.Join(" && ", winget),
            "https://www.gyan.dev/ffmpeg/builds/",
            winget);
    }

    /// <summary>Linux 包名：aria2c 来自 aria2，ffprobe/ffmpeg 同属 ffmpeg 包。</summary>
    private static string LinuxPackages(IReadOnlyList<ToolLocator.Resolution> missing)
    {
        var packages = new List<string>();
        if (missing.Any(item => string.Equals(item.Name, "aria2c", StringComparison.Ordinal)))
        {
            packages.Add("aria2");
        }

        if (missing.Any(item => !string.Equals(item.Name, "aria2c", StringComparison.Ordinal)))
        {
            packages.Add("ffmpeg");
        }

        return string.Join(" ", packages);
    }
}
