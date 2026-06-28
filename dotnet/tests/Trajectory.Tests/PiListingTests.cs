using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class PiListingTests
{
    [Fact]
    public async Task MissingPiStoreReturnsEmptyPage()
    {
        var missing = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
        var page = await TrajectoryConverter.ListPiTrajectoriesAsync(missing);

        Assert.Empty(page.Items);
        Assert.Null(page.NextCursor);
    }

    [Fact]
    public async Task PiListingUsesProjectDirectoriesAndCursorPagination()
    {
        var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
        try
        {
            var project = Path.Combine(root, "sessions", "-workspace-project");
            Directory.CreateDirectory(project);
            var older = Path.Combine(project, "2026-01-01_old.jsonl");
            var newer = Path.Combine(project, "2026-01-02_new.jsonl");
            await File.WriteAllTextAsync(older, "{}\n");
            await File.WriteAllTextAsync(newer, "{}\n");
            File.SetLastWriteTimeUtc(older, new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc));
            File.SetLastWriteTimeUtc(newer, new DateTime(2026, 1, 2, 0, 0, 0, DateTimeKind.Utc));

            var first = await TrajectoryConverter.ListPiTrajectoriesAsync(root, limit: 1);
            var second = await TrajectoryConverter.ListPiTrajectoriesAsync(
                root,
                cursor: first.NextCursor,
                limit: 1);

            Assert.Equal("2026-01-02_new", Assert.Single(first.Items).Id);
            Assert.NotNull(first.NextCursor);
            Assert.Equal("2026-01-01_old", Assert.Single(second.Items).Id);
            Assert.Null(second.NextCursor);
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, recursive: true);
            }
        }
    }
}
