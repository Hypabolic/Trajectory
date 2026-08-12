using Hypabolic.Trajectory.Streaming;

namespace Hypabolic.Trajectory.IO;

/// <summary>Explicit-root options for following a single JSONL transcript path.</summary>
public sealed class FileTrajectoryStreamOptions
{
    /// <summary>Required directory that bounds <see cref="Path"/>.</summary>
    public required string Root { get; init; }

    /// <summary>Required transcript file path (must resolve under <see cref="Root"/>).</summary>
    public required string Path { get; init; }

    public required TrajectorySource Source { get; init; }

    public string? GroupId { get; init; }

    /// <summary>Optional full stream options; when null, built from <see cref="Source"/> / <see cref="GroupId"/>.</summary>
    public StreamOptions? Stream { get; init; }

    /// <summary>Poll interval for <see cref="FileTrajectoryStream.FollowAsync"/>.</summary>
    public TimeSpan PollInterval { get; init; } = TimeSpan.FromMilliseconds(50);

    /// <summary>Full-prefix reconcile every N polls (0 = disabled).</summary>
    public int ReconcileEvery { get; init; }

    public string SourceRevision { get; init; } = "file-0";
}
