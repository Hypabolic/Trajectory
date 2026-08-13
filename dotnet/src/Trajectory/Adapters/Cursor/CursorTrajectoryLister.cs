using System.Text.Json;
using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory.Adapters.Cursor;

internal sealed class CursorTrajectoryLister : ITrajectoryLister
{
    public TrajectorySource Source => TrajectorySource.Cursor;

    public IReadOnlyList<TrajectoryListing> List(string? root)
    {
        var cursorRoot = string.IsNullOrWhiteSpace(root) ? DefaultRoot() : root;
        if (!Directory.Exists(cursorRoot)) return [];
        var metadata = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        try
        {
            foreach (var hash in Directory.EnumerateDirectories(Path.Combine(cursorRoot, "chats")))
                foreach (var session in Directory.EnumerateDirectories(hash))
                {
                    try
                    {
                        using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(session, "meta.json")));
                        var sessionId = Path.GetFileName(session);
                        if (document.RootElement.ValueKind == JsonValueKind.Object && sessionId is not null && !metadata.ContainsKey(sessionId))
                            metadata[sessionId] = document.RootElement.Clone();
                    }
                    catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException) { }
                }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }

        var items = new List<TrajectoryListing>();
        IEnumerable<string> projects;
        try { projects = Directory.EnumerateDirectories(Path.Combine(cursorRoot, "projects")); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { return []; }
        foreach (var project in projects)
        {
            var sessionsRoot = Path.Combine(project, "agent-transcripts");
            IEnumerable<string> sessions;
            try { sessions = Directory.EnumerateDirectories(sessionsRoot); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { continue; }
            foreach (var session in sessions)
            {
                var id = Path.GetFileName(session);
                if (string.IsNullOrEmpty(id)) continue;
                var path = Path.Combine(session, id + ".jsonl");
                if (!File.Exists(path)) continue;
                try
                {
                    var info = new FileInfo(path);
                    metadata.TryGetValue(id, out var meta);
                    var updated = meta.ValueKind == JsonValueKind.Object && meta.TryGetProperty("updatedAtMs", out var updatedMs) && updatedMs.TryGetInt64(out var milliseconds) && milliseconds >= 0
                        ? DateTimeOffset.FromUnixTimeMilliseconds(milliseconds)
                        : info.LastWriteTimeUtc;
                    var title = meta.ValueKind == JsonValueKind.Object && meta.TryGetProperty("title", out var titleValue) && titleValue.ValueKind == JsonValueKind.String
                        ? ListingTitle.FormatTitle(titleValue.GetString()) : null;
                    title ??= ListingTitle.DeriveCursorTitle(path);
                    items.Add(new TrajectoryListing { Id = id, Path = Path.GetFullPath(path), UpdatedAt = updated, Title = title, SizeBytes = info.Length });
                }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
            }
        }
        return items.OrderByDescending(static item => item.UpdatedAt).ThenBy(static item => item.Id, StringComparer.Ordinal).ToArray();
    }

    private static string DefaultRoot()
    {
        var configured = Environment.GetEnvironmentVariable("CURSOR_HOME")?.Trim();
        if (!string.IsNullOrEmpty(configured)) return Expand(configured);
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".cursor");
    }

    private static string Expand(string path) => path.StartsWith("~/") || path == "~"
        ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), path == "~" ? string.Empty : path[2..])
        : path;
}
