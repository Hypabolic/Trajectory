using System.Diagnostics;
using System.Text;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

/// <summary>
/// LS-11 sample CLI stream / ahp-stream coverage (.NET).
/// Spawns the unpublished Trajectory.Cli sample against temp stores and
/// FakeAhpHost fixtures only (mirrors python/tests/test_ls11_sample_cli_stream.py).
/// </summary>
public sealed class SampleCliStreamTests
{
    private const string Chat = "ahp-chat:/00000000-0000-4000-8000-0000000000c1";

    private static readonly string SessionLine =
        """{"type":"session","version":3,"id":"ls11-stream-dotnet","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}"""
        + "\n";

    private static readonly string UserLine =
        """{"type":"message","id":"m1","parentId":null,"timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"hello"}]},"sessionId":"ls11-stream-dotnet"}"""
        + "\n";

    private static string RepositoryRoot
    {
        get
        {
            // Prefer a unique monorepo marker. Do not use contracts/compatibility.json
            // alone: test output copies it to Contracts/ and macOS APFS is often
            // case-insensitive, so BaseDirectory would falsely look like the root.
            static bool IsRepoRoot(string path) =>
                File.Exists(Path.Combine(path, "dotnet", "samples", "Trajectory.Cli", "Trajectory.Cli.csproj"))
                && File.Exists(Path.Combine(path, "python", "runtime-capabilities.json"));

            // bin/Debug|Release/netX.Y → six levels up lands on repo root.
            var fromBase = Path.GetFullPath(Path.Combine(
                AppContext.BaseDirectory, "..", "..", "..", "..", "..", ".."));
            if (IsRepoRoot(fromBase))
            {
                return fromBase;
            }

            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir is not null)
            {
                if (IsRepoRoot(dir.FullName))
                {
                    return dir.FullName;
                }

                dir = dir.Parent;
            }

            throw new InvalidOperationException(
                "Could not locate repository root from AppContext.BaseDirectory.");
        }
    }

    private static string CliProjectPath =>
        Path.Combine(RepositoryRoot, "dotnet", "samples", "Trajectory.Cli", "Trajectory.Cli.csproj");

    private static string Configuration =>
#if DEBUG
        "Debug";
#else
        "Release";
#endif

    private static (int ExitCode, string Stdout, string Stderr) RunCli(
        params string[] args)
    {
        Assert.True(File.Exists(CliProjectPath), $"CLI project missing: {CliProjectPath}");

        var argumentList = new List<string>
        {
            "run",
            "--project",
            CliProjectPath,
            "-c",
            Configuration,
            "--",
        };
        argumentList.AddRange(args);

        var psi = new ProcessStartInfo
        {
            FileName = "dotnet",
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = RepositoryRoot,
        };
        foreach (var arg in argumentList)
        {
            psi.ArgumentList.Add(arg);
        }

        using var process = new Process { StartInfo = psi };
        var stdout = new StringBuilder();
        var stderr = new StringBuilder();
        process.OutputDataReceived += (_, e) =>
        {
            if (e.Data is not null)
            {
                stdout.AppendLine(e.Data);
            }
        };
        process.ErrorDataReceived += (_, e) =>
        {
            if (e.Data is not null)
            {
                stderr.AppendLine(e.Data);
            }
        };

        Assert.True(process.Start(), "failed to start trajectory CLI process");
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        var exited = process.WaitForExit(60_000);
        if (!exited)
        {
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch
            {
                // best-effort
            }

            throw new TimeoutException(
                $"CLI timed out: trajectory {string.Join(' ', args)}");
        }

        // Ensure async readers finish.
        process.WaitForExit();
        return (process.ExitCode, stdout.ToString(), stderr.ToString());
    }

    [Fact]
    public void HelpMentionsStreamAndNotADaemon()
    {
        var (code, stdout, stderr) = RunCli("--help");
        Assert.True(code == 0, $"exit={code}\nstderr={stderr}\nstdout={stdout}");
        Assert.Contains("stream", stdout, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("ahp-stream", stdout, StringComparison.OrdinalIgnoreCase);
        // Spectre lists command descriptions; stream/ahp-stream descriptions say "not a daemon".
        Assert.Contains("not a daemon", stdout, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void StreamTempFileEmitsSnapshotDeltaWithPrivacyDefault()
    {
        var root = Path.Combine(Path.GetTempPath(), "traj-ls11-dotnet-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var path = Path.Combine(root, "session.jsonl");
            File.WriteAllText(path, SessionLine + UserLine);

            var (code, stdout, stderr) = RunCli(
                "stream",
                "--source", "pi",
                "--root", root,
                "--path", path,
                "--emit", "snapshot+delta",
                "--max-updates", "1");

            Assert.True(code == 0, $"exit={code}\nstderr={stderr}\nstdout={stdout}");
            Assert.Contains("stream update", stdout, StringComparison.OrdinalIgnoreCase);
            Assert.Contains("snapshot", stdout, StringComparison.OrdinalIgnoreCase);
            Assert.Contains("delta", stdout, StringComparison.OrdinalIgnoreCase);
            Assert.Contains("Content omitted", stdout, StringComparison.Ordinal);
            Assert.Contains("not a daemon", stdout, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("hello", stdout, StringComparison.Ordinal);
        }
        finally
        {
            try
            {
                Directory.Delete(root, recursive: true);
            }
            catch
            {
                // best-effort cleanup
            }
        }
    }

    [Fact]
    public void StreamRejectsAhpSource()
    {
        var (code, stdout, stderr) = RunCli(
            "stream",
            "--source", "ahp",
            "--path", Path.Combine(Path.GetTempPath(), "x.jsonl"));

        Assert.Equal(2, code);
        var combined = stdout + "\n" + stderr;
        // Sample prints enum name InvalidInput (or snake invalid_input).
        Assert.True(
            combined.Contains("invalid_input", StringComparison.OrdinalIgnoreCase)
            || combined.Contains("InvalidInput", StringComparison.Ordinal),
            $"expected invalid_input code in:\n{combined}");
        Assert.Contains("ahp-stream", combined, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void AhpStreamFakeHostActions()
    {
        var actions = Path.Combine(
            AppContext.BaseDirectory,
            "Fixtures",
            "streaming",
            "ahp-action-turn-flow",
            "step-actions.jsonl");
        Assert.True(File.Exists(actions), $"fixture missing: {actions}");

        var (code, stdout, stderr) = RunCli(
            "ahp-stream",
            "--url", "fake://demo",
            "--chat", Chat,
            "--actions-path", actions,
            "--emit", "snapshot+delta",
            "--max-updates", "1");

        Assert.True(code == 0, $"exit={code}\nstderr={stderr}\nstdout={stdout}");
        Assert.True(
            stdout.Contains("stream update", StringComparison.OrdinalIgnoreCase)
            || stdout.Contains("ready", StringComparison.OrdinalIgnoreCase),
            $"expected stream update or ready in:\n{stdout}");
        Assert.True(
            stdout.Contains("snapshot", StringComparison.OrdinalIgnoreCase)
            || stdout.Contains("delta", StringComparison.OrdinalIgnoreCase),
            $"expected snapshot or delta in:\n{stdout}");
        Assert.Contains("Content omitted", stdout, StringComparison.Ordinal);
        Assert.DoesNotContain("test-token", stdout, StringComparison.Ordinal);
        Assert.DoesNotContain("List the files", stdout, StringComparison.Ordinal);
    }

    [Fact]
    public void AhpStreamRejectsWsUrl()
    {
        var (code, stdout, stderr) = RunCli(
            "ahp-stream",
            "--url", "ws://localhost:9999",
            "--chat", Chat);

        Assert.Equal(2, code);
        var combined = stdout + "\n" + stderr;
        Assert.Contains("fake://", combined, StringComparison.Ordinal);
        Assert.True(
            combined.Contains("invalid_input", StringComparison.OrdinalIgnoreCase)
            || combined.Contains("InvalidInput", StringComparison.Ordinal),
            $"expected invalid_input code in:\n{combined}");
    }
}
