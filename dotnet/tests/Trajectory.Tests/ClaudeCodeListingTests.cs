using Hypabolic.Trajectory.Listing;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class ClaudeCodeListingTests
{
    [Fact]
    public async Task ListsTopLevelSessionFilesNewestFirstAndPaginates()
    {
        var root = Path.Combine(
            Path.GetTempPath(),
            $"trajectory-claude-listing-{Guid.NewGuid():N}");
        try
        {
            var firstProject = Directory.CreateDirectory(
                Path.Combine(root, "project-a"));
            var secondProject = Directory.CreateDirectory(
                Path.Combine(root, "project-b"));
            var oldPath = Path.Combine(firstProject.FullName, "old-session.jsonl");
            var newPath = Path.Combine(secondProject.FullName, "new-session.jsonl");
            File.WriteAllText(oldPath, "{}");
            File.WriteAllText(newPath, "{}");
            File.WriteAllText(
                Path.Combine(firstProject.FullName, "notes.txt"),
                "not a session");
            var subagents = Directory.CreateDirectory(
                Path.Combine(firstProject.FullName, "subagents"));
            File.WriteAllText(
                Path.Combine(subagents.FullName, "agent.jsonl"),
                "{}");
            File.SetLastWriteTimeUtc(
                oldPath,
                new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc));
            File.SetLastWriteTimeUtc(
                newPath,
                new DateTime(2026, 1, 2, 0, 0, 0, DateTimeKind.Utc));

            var firstPage = await TrajectoryConverter
                .ListClaudeCodeTrajectoriesAsync(root, limit: 1);
            var secondPage = await TrajectoryConverter
                .ListClaudeCodeTrajectoriesAsync(
                    root,
                    firstPage.NextCursor,
                    limit: 1);

            Assert.Equal("new-session", Assert.Single(firstPage.Items).Id);
            Assert.NotNull(firstPage.NextCursor);
            Assert.Equal("old-session", Assert.Single(secondPage.Items).Id);
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
            $"trajectory-missing-{Guid.NewGuid():N}");

        TrajectoryListingPage page = await TrajectoryConverter
            .ListClaudeCodeTrajectoriesAsync(root);

        Assert.Empty(page.Items);
        Assert.Null(page.NextCursor);
    }
}
