using Hypabolic.Trajectory.Listing;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class CodexListingTests
{
    [Fact]
    public async Task ListsNestedRolloutsNewestFirstAndPaginates()
    {
        var root = Path.Combine(
            Path.GetTempPath(),
            $"trajectory-codex-listing-{Guid.NewGuid():N}");
        try
        {
            var oldDirectory = Directory.CreateDirectory(
                Path.Combine(root, "2026", "07", "01"));
            var newDirectory = Directory.CreateDirectory(
                Path.Combine(root, "2026", "07", "02"));
            var oldPath = Path.Combine(
                oldDirectory.FullName,
                "rollout-old.jsonl");
            var newPath = Path.Combine(
                newDirectory.FullName,
                "rollout-new.jsonl");
            File.WriteAllText(oldPath, "{}");
            File.WriteAllText(newPath, "{}");
            File.WriteAllText(
                Path.Combine(newDirectory.FullName, "notes.txt"),
                "not a rollout");
            var tooDeep = Directory.CreateDirectory(
                Path.Combine(newDirectory.FullName, "nested", "too-deep"));
            File.WriteAllText(
                Path.Combine(tooDeep.FullName, "ignored.jsonl"),
                "{}");
            File.SetLastWriteTimeUtc(
                oldPath,
                new DateTime(2026, 7, 1, 0, 0, 0, DateTimeKind.Utc));
            File.SetLastWriteTimeUtc(
                newPath,
                new DateTime(2026, 7, 2, 0, 0, 0, DateTimeKind.Utc));

            var firstPage = await TrajectoryConverter
                .ListCodexTrajectoriesAsync(root, limit: 1);
            var secondPage = await TrajectoryConverter
                .ListCodexTrajectoriesAsync(
                    root,
                    firstPage.NextCursor,
                    limit: 1);

            Assert.Equal("rollout-new", Assert.Single(firstPage.Items).Id);
            Assert.NotNull(firstPage.NextCursor);
            Assert.Equal("rollout-old", Assert.Single(secondPage.Items).Id);
            Assert.Null(secondPage.NextCursor);
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, recursive: true);
            }
        }
    }

    [Fact]
    public async Task MissingStoreReturnsAnEmptyPage()
    {
        var root = Path.Combine(
            Path.GetTempPath(),
            $"trajectory-codex-missing-{Guid.NewGuid():N}");

        TrajectoryListingPage page = await TrajectoryConverter
            .ListCodexTrajectoriesAsync(root);

        Assert.Empty(page.Items);
        Assert.Null(page.NextCursor);
    }
}
