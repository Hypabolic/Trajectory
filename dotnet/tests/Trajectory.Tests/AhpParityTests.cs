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

    [Fact]
    public void MissingStartedAtSortsLastThenByUtf8Id()
    {
        // Completed turns out of order: one missing startedAt must sort after
        // timestamped turns (nulls-last), then by UTF-8 id.
        const string snapshot = """
            {
              "ahpProtocolVersion": "0.7.0",
              "chat": {
                "resource": "ahp-chat:/sort-test",
                "turns": [
                  {
                    "id": "turn-b",
                    "message": { "text": "second", "origin": { "kind": "user" } },
                    "responseParts": [
                      { "kind": "markdown", "id": "md-b", "content": "reply-b" }
                    ]
                  },
                  {
                    "id": "turn-a",
                    "startedAt": "2026-03-15T13:00:00.000Z",
                    "message": { "text": "first", "origin": { "kind": "user" } },
                    "responseParts": [
                      { "kind": "markdown", "id": "md-a", "content": "reply-a" }
                    ]
                  },
                  {
                    "id": "turn-c",
                    "message": { "text": "third", "origin": { "kind": "user" } },
                    "responseParts": [
                      { "kind": "markdown", "id": "md-c", "content": "reply-c" }
                    ]
                  }
                ]
              }
            }
            """;

        var engine = TrajectoryEngine.CreateDefault();
        var ir = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Ahp,
            Transcript = snapshot,
        });

        var users = ir.Records
            .OfType<MessageIR>()
            .Where(static r => r.Role == TrajectoryRole.User)
            .Select(static r => r.Content)
            .ToArray();

        Assert.Equal(["first", "second", "third"], users);
    }

    [Fact]
    public void PartialModeAppendsActiveTurn()
    {
        const string snapshot = """
            {
              "ahpProtocolVersion": "0.7.0",
              "chat": {
                "resource": "ahp-chat:/partial-active",
                "turns": [
                  {
                    "id": "turn-done",
                    "startedAt": "2026-03-15T13:00:00.000Z",
                    "message": { "text": "done-user", "origin": { "kind": "user" } },
                    "responseParts": [
                      { "kind": "markdown", "id": "md-done", "content": "done-assistant" }
                    ]
                  }
                ],
                "activeTurn": {
                  "id": "turn-active",
                  "startedAt": "2026-03-15T13:01:00.000Z",
                  "message": { "text": "active-user", "origin": { "kind": "user" } },
                  "responseParts": [
                    { "kind": "markdown", "id": "md-active", "content": "active-assistant" }
                  ]
                }
              }
            }
            """;

        var engine = TrajectoryEngine.CreateDefault();
        var whole = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Ahp,
            Transcript = snapshot,
        });
        Assert.DoesNotContain(whole.Records.OfType<MessageIR>(), static r => r.Content == "active-user");
        Assert.Contains(whole.Diagnostics, static d => d.Code == DiagnosticCodes.AhpActiveTurnOmitted);

        var partial = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Ahp,
            Transcript = snapshot,
            SourceContext = new SourceContext { Partial = true },
        });
        Assert.DoesNotContain(partial.Diagnostics, static d => d.Code == DiagnosticCodes.AhpActiveTurnOmitted);
        var users = partial.Records
            .OfType<MessageIR>()
            .Where(static r => r.Role == TrajectoryRole.User)
            .Select(static r => r.Content)
            .ToArray();
        Assert.Equal(["done-user", "active-user"], users);
    }

    [Fact]
    public void SessionProviderPlumbsIntoModelInvocation()
    {
        var transcript = FixtureText("ahp/tool-calls/input.json");
        var engine = TrajectoryEngine.CreateDefault();
        var ir = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Ahp,
            Transcript = transcript,
        });

        var invocation = Assert.Single(ir.Execution.ModelInvocations);
        Assert.Equal("synthetic-provider", invocation.Provider);
    }

    [Fact]
    public void SharedCancelledTurnMatchesHypabolicGolden()
    {
        var transcript = FixtureText("ahp/cancelled-turn/input.json");
        var expectedHypabolic = FixtureText("ahp/cancelled-turn/expected.hypabolic.json");

        var engine = TrajectoryEngine.CreateDefault();
        var ir = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Ahp,
            Transcript = transcript,
        });
        var hypabolic = engine.ProjectJson(ir, OutputSchemaIds.HypabolicTrajectoryV1);
        AssertJsonEqual(expectedHypabolic, hypabolic);

        using var document = JsonDocument.Parse(hypabolic);
        var tool = Assert.Single(
            document.RootElement.GetProperty("records").EnumerateArray(),
            static r => r.GetProperty("kind").GetString() == "tool_result");
        Assert.True(tool.GetProperty("is_error").GetBoolean());
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
