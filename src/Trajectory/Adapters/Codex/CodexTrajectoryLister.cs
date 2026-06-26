using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory.Adapters.Codex;

internal sealed class CodexTrajectoryLister : ITrajectoryLister
{
    public TrajectorySource Source => TrajectorySource.Codex;

    public IReadOnlyList<TrajectoryListing> List(string? root)
    {
        var sessionsRoot = string.IsNullOrWhiteSpace(root)
            ? Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".codex",
                "sessions")
            : root;
        if (!Directory.Exists(sessionsRoot))
        {
            return [];
        }

        var items = new List<TrajectoryListing>();
        Collect(sessionsRoot, remainingDepth: 4, items);
        return items.OrderByDescending(static item => item.UpdatedAt)
            .ThenBy(static item => item.Id, StringComparer.Ordinal)
            .ToArray();
    }

    private static void Collect(
        string directory,
        int remainingDepth,
        List<TrajectoryListing> items)
    {
        if (remainingDepth < 0)
        {
            return;
        }

        try
        {
            foreach (var path in Directory.EnumerateFiles(
                         directory,
                         "*.jsonl",
                         SearchOption.TopDirectoryOnly))
            {
                try
                {
                    var info = new FileInfo(path);
                    items.Add(new TrajectoryListing
                    {
                        Id = Path.GetFileNameWithoutExtension(path),
                        Path = path,
                        UpdatedAt = info.LastWriteTimeUtc,
                        SizeBytes = info.Length,
                    });
                }
                catch (Exception error) when (
                    error is IOException or UnauthorizedAccessException)
                {
                    // The append-only store can change while being enumerated.
                }
            }

            if (remainingDepth == 0)
            {
                return;
            }

            foreach (var child in Directory.EnumerateDirectories(directory))
            {
                Collect(child, remainingDepth - 1, items);
            }
        }
        catch (Exception error) when (
            error is IOException or UnauthorizedAccessException)
        {
            // An inaccessible subtree does not make the whole store unavailable.
        }
    }
}
