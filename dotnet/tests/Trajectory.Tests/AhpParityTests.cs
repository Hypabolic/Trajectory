using System.Text.Json;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class AhpParityTests
{
    [Theory]
    [InlineData("ahp/tool-calls")]
    [InlineData("ahp/multi-turn")]
    [InlineData("ahp/cancelled-turn")]
    public void SharedFixturesMatchCanonicalGoldens(string fixture)
    {
        var transcript = FixtureText($"{fixture}/input.json");
        var expectedCanonical = FixtureText($"{fixture}/expected.canonical.json");

        var engine = TrajectoryEngine.CreateDefault();
        var ir = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Ahp,
            Transcript = transcript,
        });

        Assert.Equal("ahp", ir.SourceName);
        Assert.StartsWith("ahp-chat:/", ir.GroupId, StringComparison.Ordinal);

        var canonical = engine.ProjectJson(ir, OutputSchemaIds.LettaCanonicalV1);
        AssertJsonEqual(expectedCanonical, canonical);
    }

    [Fact]
    public void ToolCallsFixtureLinksCallAndResult()
    {
        var transcript = FixtureText("ahp/tool-calls/input.json");
        var engine = TrajectoryEngine.CreateDefault();
        var ir = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Ahp,
            Transcript = transcript,
        });

        Assert.Contains(ir.Records, static r =>
            r is AssistantToolCallsIR calls &&
            calls.ToolCalls.Any(c => c.Id == "tc-00000000-0000-4000-8000-0000000000c1"));
        Assert.Contains(ir.Records, static r =>
            r is ToolResultIR result &&
            result.ToolCallId == "tc-00000000-0000-4000-8000-0000000000c1" &&
            !result.IsError &&
            result.Content == "/workspace/demo");
    }

    [Fact]
    public void CancelledTurnDoesNotInventToolSuccess()
    {
        var transcript = FixtureText("ahp/cancelled-turn/input.json");
        var engine = TrajectoryEngine.CreateDefault();
        var ir = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Ahp,
            Transcript = transcript,
        });

        var result = Assert.Single(ir.Records.OfType<ToolResultIR>());
        Assert.Equal("tc-00000000-0000-4000-8000-0000000000c1", result.ToolCallId);
        Assert.True(result.IsError);
        Assert.Equal("User denied the tool call", result.Content);

        var call = Assert.Single(ir.Records.OfType<AssistantToolCallsIR>());
        Assert.Equal("terminal", call.ToolCalls[0].Name);
    }

    [Fact]
    public void MultiTurnConcatenatesContiguousMarkdownParts()
    {
        var transcript = FixtureText("ahp/multi-turn/input.json");
        var engine = TrajectoryEngine.CreateDefault();
        var ir = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Ahp,
            Transcript = transcript,
        });

        var assistants = ir.Records
            .OfType<MessageIR>()
            .Where(static r => r.Role == TrajectoryRole.Assistant)
            .Select(static r => r.Content)
            .ToArray();

        Assert.Equal(2, assistants.Length);
        Assert.Equal("2 + 2 equals 4.", assistants[0]);
        Assert.Equal("3 + 5 equals 8. Anything else?", assistants[1]);
    }

    [Fact]
    public async Task MissingAhpRootListsAsEmpty()
    {
        var missing = Path.Combine(
            Path.GetTempPath(),
            $"trajectory-ahp-missing-{Guid.NewGuid():N}");
        var page = await TrajectoryConverter.ListTrajectoriesAsync(
            TrajectorySource.Ahp,
            root: missing);
        Assert.Empty(page.Items);
        Assert.Null(page.NextCursor);
    }

    private static void AssertJsonEqual(string expected, string actual)
    {
        using var expectedDocument = JsonDocument.Parse(expected);
        using var actualDocument = JsonDocument.Parse(actual);
        Assert.Equal(
            JsonSerializer.Serialize(expectedDocument.RootElement),
            JsonSerializer.Serialize(actualDocument.RootElement));
    }

    private static string FixtureText(string relativePath) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", relativePath));
}
