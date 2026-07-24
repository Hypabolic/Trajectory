namespace Hypabolic.Trajectory.Listing;

public sealed record TrajectoryListing
{
    public required string Id { get; init; }
    public required string Path { get; init; }
    public DateTimeOffset? UpdatedAt { get; init; }
    public string? Title { get; init; }
    public long? SizeBytes { get; init; }
}

public sealed record ListTrajectoriesOptions
{
    public required TrajectorySource Source { get; init; }
    public string? Root { get; init; }
    public string? Cursor { get; init; }
    public int Limit { get; init; } = 50;
}

public sealed record TrajectoryListingPage
{
    public required IReadOnlyList<TrajectoryListing> Items { get; init; }
    public string? NextCursor { get; init; }
}

internal interface ITrajectoryLister
{
    TrajectorySource Source { get; }
    IReadOnlyList<TrajectoryListing> List(string? root);
}

internal static class TrajectoryPagination
{
    public static TrajectoryListingPage Paginate(
        IReadOnlyList<TrajectoryListing> items,
        string? cursor,
        int limit)
    {
        if (limit is < 1 or > 1000)
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "Listing limit must be between 1 and 1000.");
        }

        var start = 0;
        if (cursor is not null)
        {
            var state = Decode(cursor);
            var current = items.Select((item, index) => (item, index))
                .FirstOrDefault(pair => string.Equals(pair.item.Id, state.Id, StringComparison.Ordinal));
            start = current.item is not null
                ? current.index + 1
                : Math.Min(state.Index + 1, items.Count);
        }

        var page = items.Skip(start).Take(limit).ToArray();
        var end = start + page.Length;
        return new TrajectoryListingPage
        {
            Items = page,
            NextCursor = end < items.Count && page.Length > 0
                ? Encode(page[^1].Id, end - 1)
                : null,
        };
    }

    private static string Encode(string id, int index)
    {
        var bytes = System.Text.Encoding.UTF8.GetBytes($"1\n{index}\n{id}");
        return Convert.ToBase64String(bytes)
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');
    }

    private static (string Id, int Index) Decode(string cursor)
    {
        try
        {
            var padded = cursor.Replace('-', '+').Replace('_', '/');
            padded += new string('=', (4 - padded.Length % 4) % 4);
            var text = System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(padded));
            var parts = text.Split('\n', 3);
            if (parts.Length != 3 || parts[0] != "1" || !int.TryParse(parts[1], out var index) || index < 0)
            {
                throw new FormatException();
            }

            return (parts[2], index);
        }
        catch (Exception error) when (error is FormatException or ArgumentException)
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "Cursor is not a valid trajectory-listing cursor.");
        }
    }
}
