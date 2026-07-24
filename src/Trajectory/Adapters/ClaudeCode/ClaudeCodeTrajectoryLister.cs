using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory.Adapters.ClaudeCode;

internal sealed class ClaudeCodeTrajectoryLister : ITrajectoryLister
{
    public TrajectorySource Source => TrajectorySource.ClaudeCode;

    public IReadOnlyList<TrajectoryListing> List(string? root)
    {
        var projectsRoot = string.IsNullOrWhiteSpace(root)
            ? Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".claude",
                "projects")
            : root;
        if (!Directory.Exists(projectsRoot))
        {
            return [];
        }

        var items = new List<TrajectoryListing>();
        foreach (var projectDirectory in EnumerateDirectories(projectsRoot))
        {
            foreach (var path in EnumerateFiles(projectDirectory))
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
                    // The store can change while it is being enumerated.
                }
            }
        }

        return items.OrderByDescending(static item => item.UpdatedAt)
            .ThenBy(static item => item.Id, StringComparer.Ordinal)
            .ToArray();
    }

    private static IReadOnlyList<string> EnumerateDirectories(string root)
    {
        try
        {
            return Directory.EnumerateDirectories(root).ToArray();
        }
        catch (Exception error) when (
            error is IOException or UnauthorizedAccessException)
        {
            return [];
        }
    }

    private static IReadOnlyList<string> EnumerateFiles(string root)
    {
        try
        {
            return Directory.EnumerateFiles(
                    root,
                    "*.jsonl",
                    SearchOption.TopDirectoryOnly)
                .ToArray();
        }
        catch (Exception error) when (
            error is IOException or UnauthorizedAccessException)
        {
            return [];
        }
    }
}
