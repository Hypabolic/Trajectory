using System.Runtime.CompilerServices;
using Hypabolic.Trajectory.Streaming;

namespace Hypabolic.Trajectory.IO;

/// <summary>
/// Poll a single JSONL path and apply complete-line segments to core streaming.
/// Library helper only — not a daemon. Caller owns lifetime and cancellation.
/// </summary>
public sealed class FileTrajectoryStream : IAsyncDisposable, IDisposable
{
    private static readonly string MsgRootRequired = "File stream root is required.";
    private static readonly string MsgPathRequired = "File stream path is required.";
    private static readonly string MsgPathOutsideRoot = "File stream path is outside the explicit root.";
    private static readonly string MsgIoPermission = "File stream could not read the path (permission denied).";
    private static readonly string MsgIoNotFound = "File stream path was not found.";
    private static readonly string MsgIoError = "File stream I/O failed.";

    private readonly string _root;
    private readonly string _path;
    private readonly TrajectoryStreamSession _session;
    private readonly TimeSpan _pollInterval;
    private readonly int _reconcileEvery;
    private readonly string _sourceRevision;

    private long _fileOffset;
    private byte[] _hostPending = Array.Empty<byte>();
    private bool _first = true;
    private int _polls;
    private bool _closed;
    private FileIdentity? _identity;

    private FileTrajectoryStream(
        string root,
        string path,
        TrajectoryStreamSession session,
        TimeSpan pollInterval,
        int reconcileEvery,
        string sourceRevision)
    {
        _root = root;
        _path = path;
        _session = session;
        _pollInterval = pollInterval;
        _reconcileEvery = reconcileEvery;
        _sourceRevision = sourceRevision;
    }

    public string Root => _root;
    public string Path => _path;
    public StreamCursor Cursor => _session.Cursor;
    public TrajectoryStreamSession Session => _session;

    public static FileTrajectoryStream Open(FileTrajectoryStreamOptions options)
    {
        ArgumentNullException.ThrowIfNull(options);
        if (string.IsNullOrWhiteSpace(options.Root))
        {
            throw new FileStreamHostException(FileStreamHostException.RootRequired, MsgRootRequired);
        }

        if (string.IsNullOrWhiteSpace(options.Path))
        {
            throw new FileStreamHostException(FileStreamHostException.PathRequired, MsgPathRequired);
        }

        var root = System.IO.Path.GetFullPath(options.Root);
        var path = System.IO.Path.GetFullPath(options.Path);
        if (!IsUnderRoot(root, path))
        {
            throw new FileStreamHostException(
                FileStreamHostException.PathOutsideRoot,
                MsgPathOutsideRoot,
                path);
        }

        var streamOptions = options.Stream ?? new StreamOptions
        {
            Source = options.Source,
            GroupId = options.GroupId,
        };
        if (options.GroupId is not null && streamOptions.GroupId is null)
        {
            streamOptions = streamOptions with { GroupId = options.GroupId };
        }

        var session = TrajectoryStreamSession.Create(streamOptions);
        return new FileTrajectoryStream(
            root,
            path,
            session,
            options.PollInterval < TimeSpan.Zero ? TimeSpan.Zero : options.PollInterval,
            Math.Max(0, options.ReconcileEvery),
            options.SourceRevision);
    }

    /// <summary>Read growth once. Returns null when unchanged at the host edge.</summary>
    public StreamUpdate? Poll(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (_closed)
        {
            return null;
        }

        var (size, identity) = StatIdentity();
        if (size < _fileOffset)
        {
            return SnapshotFull(size, identity);
        }

        if (_first)
        {
            return SnapshotFull(size, identity);
        }

        if (IdentityChanged(identity, size))
        {
            return SnapshotFull(size, identity);
        }

        if (size > _fileOffset)
        {
            return AppendGrowth(size, identity);
        }

        _polls++;
        if (_reconcileEvery > 0 && _polls % _reconcileEvery == 0)
        {
            return ReconcileSnapshot(size, identity);
        }

        _identity = identity;
        return null;
    }

    public ValueTask<StreamUpdate?> PollAsync(CancellationToken cancellationToken = default)
    {
        // File reads are small; keep API async-friendly without thread hops.
        try
        {
            return ValueTask.FromResult(Poll(cancellationToken));
        }
        catch (OperationCanceledException)
        {
            return ValueTask.FromCanceled<StreamUpdate?>(cancellationToken);
        }
    }

    /// <summary>
    /// Yield non-empty updates until cancelled. Caller owns process lifetime (not a daemon).
    /// </summary>
    public async IAsyncEnumerable<StreamUpdate> FollowAsync(
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        while (!cancellationToken.IsCancellationRequested && !_closed)
        {
            var update = Poll(cancellationToken);
            if (update is not null && update.Kind != "unchanged")
            {
                yield return update;
            }

            if (_pollInterval > TimeSpan.Zero)
            {
                await Task.Delay(_pollInterval, cancellationToken).ConfigureAwait(false);
            }
        }
    }

    /// <summary>
    /// Finish the underlying core stream. Forwards any host-held incomplete
    /// line into core pending first so finish can commit a final unterminated
    /// line (core finish only sees core pending bytes).
    /// Host pending is retained until core apply succeeds; non-success returns
    /// without calling finish (H4).
    /// </summary>
    public StreamUpdate Finish()
    {
        if (_hostPending.Length > 0)
        {
            var update = _session.ApplyAppend(_hostPending, sourceRevision: _sourceRevision);
            if (update.Kind is not ("updated" or "unchanged"))
            {
                return update;
            }

            _hostPending = Array.Empty<byte>();
        }

        return _session.Finish();
    }

    public void Dispose() => _closed = true;

    public ValueTask DisposeAsync()
    {
        _closed = true;
        return ValueTask.CompletedTask;
    }

    private StreamUpdate SnapshotFull(long size, FileIdentity identity)
    {
        var material = ReadRange(0, size);
        _fileOffset = size;
        var (complete, pending) = TrajectoryStream.SplitCompleteLines(material);
        _hostPending = pending;
        _first = false;
        _polls++;
        _identity = identity;
        return _session.ApplySnapshot(complete, _sourceRevision);
    }

    private StreamUpdate? ReconcileSnapshot(long size, FileIdentity identity)
    {
        var material = ReadRange(0, size);
        var (complete, pending) = TrajectoryStream.SplitCompleteLines(material);
        _hostPending = pending;
        _fileOffset = size;
        _identity = identity;
        var update = _session.ApplySnapshot(complete, _sourceRevision);
        return update.Kind == "unchanged" ? null : update;
    }

    private StreamUpdate? AppendGrowth(long size, FileIdentity identity)
    {
        var chunk = ReadRange(_fileOffset, size);
        _fileOffset = size;
        var buf = Concat(_hostPending, chunk);
        var (complete, pending) = TrajectoryStream.SplitCompleteLines(buf);
        _hostPending = pending;
        _polls++;
        _identity = identity;
        if (complete.Length == 0)
        {
            return null;
        }

        var update = _session.ApplyAppend(complete, sourceRevision: _sourceRevision);
        return update.Kind == "unchanged" ? null : update;
    }

    private bool IdentityChanged(FileIdentity identity, long size)
    {
        if (_identity is null)
        {
            return false;
        }

        // File-ID change is authoritative (atomic replace / new inode).
        // Same-size in-place rewrite typically keeps the ID but updates mtime.
        if (identity.VolumeSerial != _identity.Value.VolumeSerial ||
            identity.FileIndex != _identity.Value.FileIndex)
        {
            return true;
        }

        return size == _fileOffset && identity.LastWriteTicks != _identity.Value.LastWriteTicks;
    }

    private readonly record struct FileIdentity(ulong VolumeSerial, ulong FileIndex, long LastWriteTicks);

    private (long Size, FileIdentity Identity) StatIdentity()
    {
        try
        {
            var info = new FileInfo(_path);
            info.Refresh();
            FileIdentityNative.TryGet(_path, out var volumeSerial, out var fileIndex);
            var identity = new FileIdentity(volumeSerial, fileIndex, info.LastWriteTimeUtc.Ticks);
            return (info.Length, identity);
        }
        catch (FileNotFoundException ex)
        {
            throw new FileStreamHostException(FileStreamHostException.IoNotFound, MsgIoNotFound, _path, ex);
        }
        catch (DirectoryNotFoundException ex)
        {
            throw new FileStreamHostException(FileStreamHostException.IoNotFound, MsgIoNotFound, _path, ex);
        }
        catch (UnauthorizedAccessException ex)
        {
            throw new FileStreamHostException(FileStreamHostException.IoPermission, MsgIoPermission, _path, ex);
        }
        catch (IOException ex)
        {
            throw new FileStreamHostException(FileStreamHostException.IoError, MsgIoError, _path, ex);
        }
    }

    private byte[] ReadRange(long start, long end)
    {
        if (end <= start)
        {
            return Array.Empty<byte>();
        }

        try
        {
            using var stream = new FileStream(
                _path,
                FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);
            stream.Seek(start, SeekOrigin.Begin);
            var length = checked((int)(end - start));
            var buffer = new byte[length];
            var read = 0;
            while (read < length)
            {
                var n = stream.Read(buffer, read, length - read);
                if (n == 0)
                {
                    break;
                }

                read += n;
            }

            if (read == length)
            {
                return buffer;
            }

            var trimmed = new byte[read];
            Buffer.BlockCopy(buffer, 0, trimmed, 0, read);
            return trimmed;
        }
        catch (FileNotFoundException ex)
        {
            throw new FileStreamHostException(FileStreamHostException.IoNotFound, MsgIoNotFound, _path, ex);
        }
        catch (DirectoryNotFoundException ex)
        {
            throw new FileStreamHostException(FileStreamHostException.IoNotFound, MsgIoNotFound, _path, ex);
        }
        catch (UnauthorizedAccessException ex)
        {
            throw new FileStreamHostException(FileStreamHostException.IoPermission, MsgIoPermission, _path, ex);
        }
        catch (IOException ex)
        {
            throw new FileStreamHostException(FileStreamHostException.IoError, MsgIoError, _path, ex);
        }
    }

    private static byte[] Concat(byte[] left, byte[] right)
    {
        if (left.Length == 0)
        {
            return right;
        }

        if (right.Length == 0)
        {
            return left;
        }

        var result = new byte[left.Length + right.Length];
        Buffer.BlockCopy(left, 0, result, 0, left.Length);
        Buffer.BlockCopy(right, 0, result, left.Length, right.Length);
        return result;
    }

    private static bool IsUnderRoot(string root, string path)
    {
        // Match OS path case rules: Windows is case-insensitive; Unix/macOS
        // default to case-sensitive so a differently-cased sibling of root
        // cannot pass containment (LS-09 path_outside_root).
        var comparison = OperatingSystem.IsWindows()
            ? StringComparison.OrdinalIgnoreCase
            : StringComparison.Ordinal;
        var rootFull = root.TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar)
            + System.IO.Path.DirectorySeparatorChar;
        var pathFull = path;
        return pathFull.StartsWith(rootFull, comparison)
            || string.Equals(
                pathFull.TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar),
                root.TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar),
                comparison);
    }
}
