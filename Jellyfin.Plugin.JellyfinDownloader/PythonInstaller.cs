using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;

namespace Jellyfin.Plugin.JellyfinDownloader;

/// <summary>
/// 「安装 Python」按钮的后端逻辑：只做**无需 sudo 的引导**，不静默安装、不代跑 sudo。
///
/// macOS 上最轻的官方途径是命令行开发者工具（自带 /usr/bin/python3）：
/// 调 `xcode-select --install` 会弹出系统安装对话框，用户确认后即可。
/// 其它平台给出对应包管理器命令与官网下载页。
/// </summary>
public static class PythonInstaller
{
    /// <summary>给出安装途径；macOS 上会尝试唤出系统安装对话框。</summary>
    public static (bool Ok, string Detail, string Command, string Url, IReadOnlyList<string> Options) Guide()
    {
        if (OperatingSystem.IsMacOS())
        {
            var options = new List<string>
            {
                "xcode-select --install（系统手段，自带 /usr/bin/python3，无需 sudo）",
                "brew install python（已装 Homebrew 时最快）",
                "到 python.org 下载 macOS 安装包",
            };

            var (started, message) = TryRun("/usr/bin/xcode-select", "--install");
            string detail;
            if (started)
            {
                detail = "已弹出「安装命令行开发者工具」对话框：确认安装后系统会提供 /usr/bin/python3（无需 sudo）。装完回到本页点「刷新状态」。";
            }
            else if (message.Contains("already installed", StringComparison.OrdinalIgnoreCase))
            {
                // 按钮常驻后，在已装好的机器上点它属于正常情况，别报成错误
                detail = "本机的命令行开发者工具已安装，/usr/bin/python3 可以直接用。要装更新的版本，可以用下面的命令或 python.org 安装包。";
            }
            else
            {
                detail = "未能自动唤出安装对话框（" + message + "）。可以手动执行下面的命令，或从 python.org 下载安装包。";
            }

            return (started, detail, "xcode-select --install", "https://www.python.org/downloads/macos/", options);
        }

        if (OperatingSystem.IsLinux())
        {
            var command = File.Exists("/usr/bin/dnf") || File.Exists("/usr/bin/yum")
                ? "sudo dnf install -y python3"
                : "sudo apt-get install -y python3";
            return (
                false,
                "Linux 上装 Python 需要包管理器权限，本插件不会代跑 sudo。请在终端执行下面的命令，然后回来点「刷新状态」。",
                command,
                "https://www.python.org/downloads/source/",
                new List<string> { command, "或从 python.org 源码编译安装" });
        }

        return (
            false,
            "Windows 上请用 winget 或官网安装包安装 Python 3，安装时记得勾选 “Add python.exe to PATH”。",
            "winget install Python.Python.3.12",
            "https://www.python.org/downloads/windows/",
            new List<string> { "winget install Python.Python.3.12", "到 python.org 下载 Windows 安装包" });
    }

    private static (bool Ok, string Message) TryRun(string file, string arguments)
    {
        try
        {
            if (!File.Exists(file))
            {
                return (false, file + " 不存在");
            }

            var info = new ProcessStartInfo
            {
                FileName = file,
                Arguments = arguments,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };

            using var process = Process.Start(info);
            if (process is null)
            {
                return (false, "进程未能启动");
            }

            var output = process.StandardOutput.ReadToEnd() + process.StandardError.ReadToEnd();
            process.WaitForExit(10000);

            // xcode-select 在「已装过」时会以非 0 退出，这也是有效信息
            var trimmed = output.Trim();
            return (process.ExitCode == 0, trimmed.Length > 0 ? trimmed : "exit " + process.ExitCode);
        }
        catch (Exception ex)
        {
            return (false, ex.Message);
        }
    }
}
