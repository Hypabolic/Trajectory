using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory.Adapters.Pi;

internal sealed class PiTrajectoryLister : ITrajectoryLister
{
    public TrajectorySource Source => TrajectorySource.Pi;

    public IReadOnlyList<TrajectoryListing> List(string? root)
    {
        var agentRoot = string.IsNullOrWhiteSpace(root) ? DefaultAgentRoot() : root;
        var sessionsRoot = Path.Combine(agentRoot, "sessions");
        if (!Directory.Exists(sessionsRoot))
        {
            return [];
        }

        var items = new List<TrajectoryListing>();
        IEnumerable<string> projectDirectories;
        try
        {
            projectDirectories = Directory.EnumerateDirectories(sessionsRoot).ToArray();
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            return [];
        }

        foreach (var projectDirectory in projectDirectories)
        {
            IEnumerable<string> files;
            try
            {
                files = Directory.EnumerateFiles(projectDirectory, "*.jsonl", SearchOption.TopDirectoryOnly)
                    .ToArray();
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            {
                continue;
            }

            foreach (var path in files)
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
                catch (Exception error) when (error is IOException or UnauthorizedAccessException)
                {
                    // The store can change while it is being enumerated.
                }
            }
        }

        return items.OrderByDescending(static item => item.UpdatedAt)
            .ThenBy(static item => item.Id, StringComparer.Ordinal)
            .ToArray();
    }

    private static string DefaultAgentRoot()
    {
        var configured = Environment.GetEnvironmentVariable("PI_CODING_AGENT_DIR")?.Trim();
        return string.IsNullOrEmpty(configured)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".pi", "agent")
            : configured;
    }
}
