using System.Text;
using System.Text.Json.Nodes;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class CodexParityTests
{
    [Theory]
    [InlineData("full")]
    [InlineData("chunks")]
    public void SanitizedFixturesMatchPinnedUpstreamOutputs(string fixtureName)
    {
        var input = Fixture($"Codex/{fixtureName}/input.jsonl");
        var expected = JsonNode.Parse(
            Fixture($"Codex/{fixtureName}/expected.json"))!;
        var expectedCanonical = JsonNode.Parse(
            Fixture($"Codex/{fixtureName}/canonical-expected.json"))!;
        var trajectory = TrajectoryConverter.NormalizeToIR(
            TrajectorySource.Codex,
            input);
        var actual = JsonNode.Parse(TrajectoryConverter.NormalizeJson(
            TrajectorySource.Codex,
            input,
            OutputSchemaIds.LettaTrajectoryV1))!;
        var actualCanonical = JsonNode.Parse(TrajectoryConverter.NormalizeJson(
            TrajectorySource.Codex,
            input,
            OutputSchemaIds.LettaCanonicalV1))!;

        Assert.True(JsonNode.DeepEquals(expected["records"], actual));
        Assert.True(JsonNode.DeepEquals(
            expected["diagnostics"],
            DiagnosticsNode(trajectory.Diagnostics)));
        Assert.True(JsonNode.DeepEquals(expectedCanonical, actualCanonical));
    }

    [Fact]
    public void MissingAndConflictingSourceGroupsFailWithTypedCodes()
    {
        const string noMetadata = """
            {"timestamp":"2026-07-03T10:00:00.000Z","type":"response_item","payload":{"type":"message","role":"user","content":"start"}}
            {"timestamp":"2026-07-03T10:00:01.000Z","type":"response_item","payload":{"type":"message","role":"assistant","content":"done"}}
            """;

        var missing = Assert.Throws<TrajectoryNormalizationException>(() =>
            TrajectoryConverter.NormalizeToCanonical(
                TrajectorySource.Codex,
                noMetadata));
        var compatibleTrajectory = TrajectoryConverter.NormalizeTranscript(
            TrajectorySource.Codex,
            noMetadata);
        var conflicting = Assert.Throws<TrajectoryNormalizationException>(() =>
            TrajectoryConverter.NormalizeToCanonical(
                TrajectorySource.Codex,
                Fixture("Codex/full/input.jsonl"),
                sourceContext: new SourceContext
                {
                    GroupId = "different-session",
                }));

        Assert.Equal(NormalizationErrorCode.SourceGroupRequired, missing.Code);
        Assert.Equal(NormalizationErrorCode.SourceGroupConflict, conflicting.Code);
        Assert.Equal(3, compatibleTrajectory.Records.Count);
    }

    [Fact]
    public void ArbitraryChunksPreserveWholeRolloutCanonicalIdentity()
    {
        var input = Fixture("Codex/chunks/input.jsonl");
        var whole = TrajectoryConverter.NormalizeToCanonical(
            TrajectorySource.Codex,
            input);

        var splitBeforeCall = NormalizeChunks(input, splitAfterLine: 2);
        var splitBeforeResult = NormalizeChunks(input, splitAfterLine: 3);
        var reversedArrival = NormalizeChunks(
            input,
            splitAfterLine: 3,
            reverseArrival: true);

        AssertRecordsEqual(whole.Records, splitBeforeCall);
        AssertRecordsEqual(whole.Records, splitBeforeResult);
        AssertRecordsEqual(whole.Records, reversedArrival);
    }

    [Fact]
    public void CustomAndToolSearchPairsSurviveWithCodexProvenance()
    {
        var input = Fixture("Codex/full/input.jsonl");
        var trajectory = TrajectoryConverter.NormalizeToIR(
            TrajectorySource.Codex,
            input);
        var hypabolic = TrajectoryConverter.NormalizeToHypabolic(
            TrajectorySource.Codex,
            input);

        Assert.Equal("codex-session-1", trajectory.GroupId);
        Assert.Equal("0.140.0", trajectory.ProducerVersion);
        Assert.Equal("codex-session-1", hypabolic.Source.GroupId);
        Assert.Equal("0.140.0", hypabolic.Source.ProducerVersion);
        Assert.Contains(
            hypabolic.Records,
            static record => record.ToolCalls?.Any(
                static call => call.Name == "apply_patch") == true);
        Assert.Contains(
            hypabolic.Records,
            static record => record.ToolCalls?.Any(
                static call => call.Name == "tool_search") == true);
        Assert.Contains(
            hypabolic.Records,
            static record => record.ToolCallId == "call-search");
        Assert.All(
            hypabolic.Records.Where(static record => record.Role != "meta"),
            static record =>
            {
                Assert.Equal("location", record.Provenance.SourceIdentityKind);
                Assert.Equal("byte", record.Provenance.SourceAnchorKind);
                Assert.NotNull(record.Provenance.SourceOffset);
                Assert.Equal("0.140.0", record.Provenance.ProducerVersion);
            });
        Assert.Empty(trajectory.Execution.ModelInvocations);
    }

    private static IReadOnlyList<LettaCanonicalRecord> NormalizeChunks(
        string transcript,
        int splitAfterLine,
        bool reverseArrival = false)
    {
        var split = NthNewlineEnd(transcript, splitAfterLine);
        var initial = transcript[..split];
        var continuation = transcript[split..];
        var continuationContext = new SourceContext
        {
            GroupId = "codex-chunk-session",
            BaseByteOffset = Encoding.UTF8.GetByteCount(initial),
            Partial = true,
        };
        var initialContext = new SourceContext { Partial = true };

        LettaCanonicalResult first;
        LettaCanonicalResult second;
        if (reverseArrival)
        {
            second = TrajectoryConverter.NormalizeToCanonical(
                TrajectorySource.Codex,
                continuation,
                sourceContext: continuationContext);
            first = TrajectoryConverter.NormalizeToCanonical(
                TrajectorySource.Codex,
                initial,
                sourceContext: initialContext);
        }
        else
        {
            first = TrajectoryConverter.NormalizeToCanonical(
                TrajectorySource.Codex,
                initial,
                sourceContext: initialContext);
            second = TrajectoryConverter.NormalizeToCanonical(
                TrajectorySource.Codex,
                continuation,
                sourceContext: continuationContext);
        }

        return [.. first.Records, .. second.Records];
    }

    private static void AssertRecordsEqual(
        IReadOnlyList<LettaCanonicalRecord> expected,
        IReadOnlyList<LettaCanonicalRecord> actual)
    {
        Assert.Equal(
            expected.Select(static record => record.RecordId),
            actual.Select(static record => record.RecordId));
        Assert.Equal(
            expected.Select(static record => record.StableSourceRecordId),
            actual.Select(static record => record.StableSourceRecordId));
        Assert.Equal(
            expected.Select(static record => record.SourceOrderId),
            actual.Select(static record => record.SourceOrderId));
        Assert.Equal(
            expected.Select(static record => record.RecordJson),
            actual.Select(static record => record.RecordJson));
    }

    private static int NthNewlineEnd(string value, int count)
    {
        var offset = 0;
        for (var index = 0; index < count; index++)
        {
            offset = value.IndexOf('\n', offset) + 1;
            Assert.True(offset > 0);
        }

        return offset;
    }

    private static JsonArray DiagnosticsNode(
        IReadOnlyList<TrajectoryDiagnostic> diagnostics) =>
        new(diagnostics.Select(static diagnostic =>
        {
            var node = new JsonObject
            {
                ["code"] = diagnostic.Code,
                ["message"] = diagnostic.Message,
            };
            if (diagnostic.InputLine is { } inputLine)
                node["inputLine"] = inputLine;
            if (diagnostic.RecordIndex is { } recordIndex)
                node["recordIndex"] = recordIndex;
            if (diagnostic.Count is { } count)
                node["count"] = count;
            return node;
        }).ToArray());

    private static string Fixture(string relativePath) =>
        File.ReadAllText(Path.Combine(
            AppContext.BaseDirectory,
            "Fixtures",
            relativePath));
}
