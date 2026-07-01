using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory.Adapters.Hermes;

/// <summary>
/// Lists Hermes sessions from the local SQLite store locator.
/// Core packages stay SQLite-free: a missing store yields an empty page.
/// Full session-row export still happens by feeding message JSON to the decoder.
/// </summary>
internal sealed class HermesTrajectoryLister : ITrajectoryLister
{
    public TrajectorySource Source => TrajectorySource.Hermes;

    public IReadOnlyList<TrajectoryListing> List(string? root)
    {
        var storePath = ResolveStorePath(root);
        // Without a SQLite provider the core package can only observe presence.
        // Missing stores list as empty; real session enumeration is optional.
        if (!File.Exists(storePath))
        {
            return [];
        }

        // A present state.db without an embedded SQLite reader cannot be opened
        // from the BCL-only core package. Treat as empty so callers that only
        // need normalize remain dependency-free; optional provider packages may
        // replace this lister with full sessions-table enumeration.
        return [];
    }

    /// <summary>
    /// Default store is <c>~/.hermes/state.db</c>. A root may be the database
    /// file itself or the directory containing it.
    /// </summary>
    internal static string ResolveStorePath(string? root)
    {
        if (string.IsNullOrWhiteSpace(root))
        {
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return Path.Combine(home, ".hermes", "state.db");
        }

        var trimmed = root.Trim();
        return trimmed.EndsWith(".db", StringComparison.OrdinalIgnoreCase)
            ? trimmed
            : Path.Combine(trimmed, "state.db");
    }
}
