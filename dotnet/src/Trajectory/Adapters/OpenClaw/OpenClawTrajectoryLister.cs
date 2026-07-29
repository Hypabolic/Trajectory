using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory.Adapters.OpenClaw;

internal sealed class OpenClawTrajectoryLister : ITrajectoryLister
{
    public TrajectorySource Source => TrajectorySource.OpenClaw;

    public IReadOnlyList<TrajectoryListing> List(string? root)
    {
        var stateRoot = string.IsNullOrWhiteSpace(root) ? DefaultStateRoot() : root;
        var agentsRoot = Path.Combine(stateRoot, "agents");
        if (!Directory.Exists(agentsRoot))
        {
            return [];
        }

        var items = new List<TrajectoryListing>();
        IEnumerable<string> agentDirectories;
        try
        {
            agentDirectories = Directory.EnumerateDirectories(agentsRoot).ToArray();
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            return [];
        }

        foreach (var agentDirectory in agentDirectories)
        {
            var sessionsDirectory = Path.Combine(agentDirectory, "sessions");
            if (!Directory.Exists(sessionsDirectory))
            {
                continue;
            }

            IEnumerable<string> files;
            try
            {
                files = Directory.EnumerateFiles(sessionsDirectory, "*.jsonl", SearchOption.TopDirectoryOnly)
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

    private static string DefaultStateRoot()
    {
        foreach (var name in new[] { "OPENCLAW_STATE_DIR", "CLAWDBOT_STATE_DIR" })
        {
            var configured = Environment.GetEnvironmentVariable(name)?.Trim();
            if (!string.IsNullOrEmpty(configured))
            {
                return configured;
            }
        }

        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var openclaw = Path.Combine(home, ".openclaw");
        if (Directory.Exists(openclaw))
        {
            return openclaw;
        }

        return Path.Combine(home, ".clawdbot");
    }
}
