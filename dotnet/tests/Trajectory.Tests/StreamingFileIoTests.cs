using Hypabolic.Trajectory.IO;
using Hypabolic.Trajectory.Streaming;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class StreamingFileIoTests
{
    private static readonly byte[] SessionLine =
        """{"type":"session","version":3,"id":"stream-file-io-dotnet","timestamp":"2026-01-01T00:00:00.000Z","cwd":"/workspace/demo"}"""u8.ToArray()
        .Concat(new byte[] { (byte)'\n' }).ToArray();

    private static readonly byte[] UserLine =
        """{"type":"message","id":"m1","parentId":null,"timestamp":"2026-01-01T00:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"hello"}]},"sessionId":"stream-file-io-dotnet"}"""u8.ToArray()
        .Concat(new byte[] { (byte)'\n' }).ToArray();

    [Fact]
    public void GrowthAndIncompleteLine()
    {
        var root = CreateTempRoot();
        var path = Path.Combine(root, "session.jsonl");
        File.WriteAllBytes(path, Array.Empty<byte>());

        using var stream = FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
        {
            Root = root,
            Path = path,
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-io-dotnet",
        });

        var u0 = stream.Poll();
        Assert.NotNull(u0);
        Assert.Equal("updated", u0!.Kind);
        Assert.NotNull(u0.Snapshot);
        Assert.Empty(u0.Snapshot!.Records);

        var incomplete = SessionLine.Concat(UserLine.Take(40)).ToArray();
        File.WriteAllBytes(path, incomplete);
        var u1 = stream.Poll();
        Assert.NotNull(u1);
        Assert.Equal("updated", u1!.Kind);
        // Session meta committed; incomplete user line held at host — not materialized.
        var recordsAfterPartial = u1.Snapshot!.Records.Count;
        Assert.True(recordsAfterPartial >= 1);
        Assert.DoesNotContain(u1.Snapshot.Records, static r =>
            r.Record.TryGetValue("role", out var role) && role?.ToString() == "user");

        File.WriteAllBytes(path, SessionLine.Concat(UserLine).ToArray());
        var u2 = stream.Poll();
        Assert.NotNull(u2);
        Assert.Equal("updated", u2!.Kind);
        Assert.NotNull(u2.Snapshot);
        Assert.True(u2.Snapshot!.Records.Count > recordsAfterPartial);
        Assert.Contains(u2.Snapshot.Records, static r =>
            r.Record.TryGetValue("role", out var role) && role?.ToString() == "user");
        foreach (var d in u2.Diagnostics)
        {
            Assert.DoesNotContain(path, d.Message, StringComparison.Ordinal);
        }
    }

    [Fact]
    public void Finish_FlushesHostPending()
    {
        var root = CreateTempRoot();
        var path = Path.Combine(root, "session.jsonl");
        // Complete session + incomplete user line (no trailing LF).
        var incompleteUser = UserLine.AsSpan(0, UserLine.Length - 1).ToArray();
        File.WriteAllBytes(path, SessionLine.Concat(incompleteUser).ToArray());

        using var stream = FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
        {
            Root = root,
            Path = path,
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-io-dotnet",
        });

        var u0 = stream.Poll();
        Assert.NotNull(u0);
        Assert.Equal("updated", u0!.Kind);
        Assert.DoesNotContain(u0.Snapshot!.Records, static r =>
            r.Record.TryGetValue("role", out var role) && role?.ToString() == "user");
        var recordsBeforeFinish = u0.Snapshot.Records.Count;

        var finished = stream.Finish();
        Assert.True(finished.Kind is "updated" or "unchanged");
        Assert.True(stream.Session.State.Finished);
        Assert.True(finished.Snapshot!.Records.Count > recordsBeforeFinish);
        Assert.Contains(finished.Snapshot.Records, static r =>
            r.Record.TryGetValue("role", out var role) && role?.ToString() == "user");
    }

    [Fact]
    public void CoalescedGrowth()
    {
        var root = CreateTempRoot();
        var path = Path.Combine(root, "session.jsonl");
        File.WriteAllBytes(path, SessionLine);

        using var stream = FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
        {
            Root = root,
            Path = path,
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-io-dotnet",
        });
        Assert.NotNull(stream.Poll());

        File.WriteAllBytes(path, SessionLine.Concat(UserLine).ToArray());
        var update = stream.Poll();
        Assert.NotNull(update);
        Assert.Equal("updated", update!.Kind);
        Assert.True(update.Snapshot!.Records.Count >= 1);
    }

    [Fact]
    public void TruncationSurfacesCoreReset()
    {
        var root = CreateTempRoot();
        var path = Path.Combine(root, "session.jsonl");
        File.WriteAllBytes(path, SessionLine.Concat(UserLine).ToArray());

        using var stream = FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
        {
            Root = root,
            Path = path,
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-io-dotnet",
        });
        var first = stream.Poll();
        Assert.NotNull(first);
        Assert.Equal("updated", first!.Kind);

        File.WriteAllBytes(path, SessionLine);
        var update = stream.Poll();
        Assert.NotNull(update);
        Assert.Equal("reset-required", update!.Kind);
        Assert.NotNull(update.Reset);
    }

    [Fact]
    public void PathOutsideRootIsHostError()
    {
        var root = CreateTempRoot();
        var outsideDir = Path.Combine(Path.GetTempPath(), "traj-io-out-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(outsideDir);
        var outside = Path.Combine(outsideDir, "x.jsonl");
        File.WriteAllBytes(outside, "\n"u8.ToArray());

        var ex = Assert.Throws<FileStreamHostException>(() =>
            FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
            {
                Root = root,
                Path = outside,
                Source = TrajectorySource.Pi,
            }));
        Assert.Equal(FileStreamHostException.PathOutsideRoot, ex.Code);
        Assert.Equal("File stream path is outside the explicit root.", ex.Message);
    }

    /// On case-sensitive filesystems, a differently-cased sibling of root must
    /// not pass explicit-root containment (LS-09 path_outside_root).
    [Fact]
    public void CaseDifferingSiblingRootIsOutsideOnCaseSensitiveFileSystems()
    {
        if (OperatingSystem.IsWindows())
        {
            return;
        }

        var parent = Path.Combine(Path.GetTempPath(), "traj-io-case-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(parent);
        var rootA = Path.Combine(parent, "RootDir");
        var rootB = Path.Combine(parent, "rootdir");
        Directory.CreateDirectory(rootA);
        // Case-insensitive volumes treat RootDir/rootdir as the same entry.
        if (Directory.Exists(rootB))
        {
            try { Directory.Delete(parent, recursive: true); } catch { /* best-effort */ }
            return;
        }

        Directory.CreateDirectory(rootB);
        var pathInB = Path.Combine(rootB, "x.jsonl");
        File.WriteAllBytes(pathInB, "\n"u8.ToArray());
        try
        {
            var ex = Assert.Throws<FileStreamHostException>(() =>
                FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
                {
                    Root = rootA,
                    Path = pathInB,
                    Source = TrajectorySource.Pi,
                }));
            Assert.Equal(FileStreamHostException.PathOutsideRoot, ex.Code);
        }
        finally
        {
            try { Directory.Delete(parent, recursive: true); } catch { /* best-effort */ }
        }
    }

    [Fact]
    public void RootRequired()
    {
        var ex = Assert.Throws<FileStreamHostException>(() =>
            FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
            {
                Root = "  ",
                Path = "/tmp/x.jsonl",
                Source = TrajectorySource.Pi,
            }));
        Assert.Equal(FileStreamHostException.RootRequired, ex.Code);
    }

    [Fact]
    public void PermissionDeniedIsHostError()
    {
        if (OperatingSystem.IsWindows())
        {
            return;
        }

        var root = CreateTempRoot();
        var path = Path.Combine(root, "session.jsonl");
        File.WriteAllBytes(path, SessionLine);
        File.SetUnixFileMode(path, (UnixFileMode)0);
        try
        {
            using var stream = FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
            {
                Root = root,
                Path = path,
                Source = TrajectorySource.Pi,
                GroupId = "x",
            });
            var ex = Assert.Throws<FileStreamHostException>(() => stream.Poll());
            Assert.True(
                ex.Code is FileStreamHostException.IoPermission or FileStreamHostException.IoError);
            Assert.DoesNotContain(path, ex.Message, StringComparison.Ordinal);
        }
        finally
        {
            File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        }
    }

    [Fact]
    public void SplitCompleteLinesHoldsIncomplete()
    {
        var (complete, pending) = TrajectoryStream.SplitCompleteLines("abc\ndef"u8.ToArray());
        Assert.Equal("abc\n"u8.ToArray(), complete);
        Assert.Equal("def"u8.ToArray(), pending);
    }

    [Fact]
    public void Finish_FailedPendingFlush_RetainsHostBuffer()
    {
        var root = CreateTempRoot();
        var path = Path.Combine(root, "session.jsonl");
        File.WriteAllBytes(path, Array.Empty<byte>());

        using var stream = FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
        {
            Root = root,
            Path = path,
            Source = TrajectorySource.Pi,
            GroupId = "stream-file-io-dotnet",
            Stream = new StreamOptions
            {
                Source = TrajectorySource.Pi,
                GroupId = "stream-file-io-dotnet",
                MaxPendingBytes = 16,
                MaxLineBytes = 16,
            },
        });

        var u0 = stream.Poll();
        Assert.NotNull(u0);
        Assert.Equal("updated", u0!.Kind);
        var cursorBefore = stream.Cursor;
        Assert.False(stream.Session.State.Finished);

        var incomplete = System.Text.Encoding.UTF8.GetBytes(
            "{\"type\":\"message\",\"id\":\"pending-too-long\",\"x\":\"" + new string('y', 80));
        File.WriteAllBytes(path, incomplete);
        Assert.Null(stream.Poll());

        var finished = stream.Finish();
        Assert.Equal("error", finished.Kind);
        Assert.NotNull(finished.Error);
        Assert.Equal("stream_buffer_limit", finished.Error!.Value.Code);
        Assert.False(stream.Session.State.Finished);
        Assert.Equal(cursorBefore.Generation, stream.Cursor.Generation);

        var again = stream.Finish();
        Assert.Equal("error", again.Kind);
        Assert.Equal("stream_buffer_limit", again.Error!.Value.Code);
        Assert.False(stream.Session.State.Finished);
    }

    private static string CreateTempRoot()
    {
        var root = Path.Combine(Path.GetTempPath(), "traj-io-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }
}
