using System.Text;
using System.Text.Json.Nodes;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class ClaudeCodeParityTests
{
    [Theory]
    [InlineData("tool-call")]
    [InlineData("cleanup")]
    [InlineData("mixed-version")]
    public void SanitizedVersionFamilyFixturesMatchPinnedUpstreamTrajectory(
        string fixtureName)
    {
        var input = Fixture($"ClaudeCode/{fixtureName}/input.jsonl");
        var expected = JsonNode.Parse(
            Fixture($"ClaudeCode/{fixtureName}/expected.json"))!;
        var actualRecords = JsonNode.Parse(TrajectoryConverter.NormalizeJson(
            TrajectorySource.ClaudeCode,
            input,
            OutputSchemaIds.LettaTrajectoryV1))!;
        var trajectory = TrajectoryConverter.NormalizeToIR(
            TrajectorySource.ClaudeCode,
            input);

        Assert.True(JsonNode.DeepEquals(expected["records"], actualRecords));
        Assert.True(JsonNode.DeepEquals(
            expected["diagnostics"],
            DiagnosticsNode(trajectory.Diagnostics)));
    }

    [Fact]
    public void CanonicalProjectionMatchesPinnedUpstreamGolden()
    {
        var input = Fixture("ClaudeCode/tool-call/input.jsonl");
        var expected = JsonNode.Parse(
            Fixture("ClaudeCode/tool-call/canonical-expected.json"))!;
        var actual = JsonNode.Parse(TrajectoryConverter.NormalizeJson(
            TrajectorySource.ClaudeCode,
            input,
            OutputSchemaIds.LettaCanonicalV1))!;

        Assert.True(JsonNode.DeepEquals(expected, actual));
    }

    [Fact]
    public void IdlessRowsKeepIdentityAcrossAbsoluteChunkOffsets()
    {
        const string user = """
            {"type":"user","sessionId":"idless-session","timestamp":"2026-05-01T10:00:00.000Z","message":{"role":"user","content":"start"}}
            """;
        const string assistant = """
            {"type":"assistant","sessionId":"idless-session","timestamp":"2026-05-01T10:00:01.000Z","message":{"role":"assistant","model":"test-model","content":[{"type":"text","text":"done"}]}}
            """;
        var whole = TrajectoryConverter.NormalizeToCanonical(
            TrajectorySource.ClaudeCode,
            $"{user}\n{assistant}");
        var chunk = TrajectoryConverter.NormalizeToCanonical(
            TrajectorySource.ClaudeCode,
            assistant,
            sourceContext: new SourceContext
            {
                GroupId = "idless-session",
                BaseByteOffset = Encoding.UTF8.GetByteCount(user + "\n"),
                Partial = true,
            });
        var wholeAssistant = whole.Records.Single(
            static record => record.RecordType == "assistant");
        var chunkAssistant = Assert.Single(chunk.Records);

        Assert.Equal(SourceIdentityKind.Location.ToString().ToLowerInvariant(),
            wholeAssistant.SourceIdentityKind);
        Assert.Equal(wholeAssistant.StableSourceRecordId,
            chunkAssistant.StableSourceRecordId);
        Assert.Equal(wholeAssistant.RecordId, chunkAssistant.RecordId);
    }

    [Fact]
    public void MixedVersionsAndInvocationMetadataRoundTripWithoutHeuristics()
    {
        var input = Fixture("ClaudeCode/mixed-version/input.jsonl");
        var trajectory = TrajectoryConverter.NormalizeToIR(
            TrajectorySource.ClaudeCode,
            input);
        var hypabolic = TrajectoryConverter.NormalizeToHypabolic(
            TrajectorySource.ClaudeCode,
            input);
        var hypabolicJson = TrajectoryConverter.NormalizeJson(
            TrajectorySource.ClaudeCode,
            input,
            OutputSchemaIds.HypabolicTrajectoryV1);

        Assert.Equal("2.1.139", trajectory.ProducerVersion);
        Assert.Equal("2.1.139", hypabolic.Source.ProducerVersion);
        Assert.Contains(
            hypabolic.Records,
            static record => record.Provenance.ProducerVersion == "2.1.139");
        Assert.Contains(
            hypabolic.Records,
            static record => record.Provenance.ProducerVersion == "2.1.206");

        Assert.Equal(2, trajectory.Execution.ModelInvocations.Count);
        var legacy = trajectory.Execution.ModelInvocations[0];
        Assert.Null(legacy.Provider);
        Assert.Null(legacy.ApiFamily);
        Assert.Null(legacy.RequestedModel);
        Assert.Equal("claude-sonnet-4", legacy.ResponseModel);
        Assert.Equal("msg_legacy", legacy.ResponseId);
        Assert.Equal("tool_use", legacy.StopReason);
        Assert.Equal("2.1.139", legacy.ProducerVersion);
        Assert.Equal(100L, legacy.Usage!.InputTokens);
        Assert.Equal(20L, legacy.Usage.OutputTokens);
        Assert.Equal(10L, legacy.Usage.CacheReadTokens);
        Assert.Equal(5L, legacy.Usage.CacheWriteTokens);
        Assert.Null(legacy.Usage.TotalTokens);
        Assert.Null(legacy.StartedAt);
        Assert.Null(legacy.FirstResponseAt);
        Assert.Equal(
            DateTimeOffset.Parse("2026-07-01T09:00:02Z"),
            legacy.CompletedAt);
        Assert.Equal(
            "2.1.206",
            JsonNode.Parse(hypabolicJson)!["records"]!
                .AsArray()
                .Select(static record =>
                    record!["provenance"]?["producer_version"]?.GetValue<string>())
                .Last(static version => version is not null));
    }

    [Fact]
    public void ContextAndIdentityAreIndependentOfTransportArrivalOrder()
    {
        const string early = """
            {"type":"user","uuid":"user-1","sessionId":"order-session","cwd":"/repo/early","gitBranch":"main","timestamp":"2026-05-01T10:00:00.000Z","message":{"role":"user","content":"start"}}
            """;
        const string late = """
            {"type":"assistant","uuid":"assistant-1","sessionId":"order-session","cwd":"/repo/late","gitBranch":"other","timestamp":"2026-05-01T10:00:01.000Z","message":{"role":"assistant","model":"test-model","content":[{"type":"text","text":"done"}]}}
            """;
        var forward = TrajectoryConverter.NormalizeToCanonical(
            TrajectorySource.ClaudeCode,
            $"{early}\n{late}");
        var reverse = TrajectoryConverter.NormalizeToCanonical(
            TrajectorySource.ClaudeCode,
            $"{late}\n{early}");

        Assert.Equal(forward.Records[0].ContentHash, reverse.Records[0].ContentHash);
        Assert.Equal(
            forward.Records.Skip(1)
                .Select(static record => record.RecordId)
                .Order(StringComparer.Ordinal),
            reverse.Records.Skip(1)
                .Select(static record => record.RecordId)
                .Order(StringComparer.Ordinal));
        Assert.Contains("\"cwd\":\"/repo/early\"", forward.Records[0].RecordJson);
        Assert.Contains("\"git_branch\":\"main\"", forward.Records[0].RecordJson);
    }

    [Fact]
    public void ConflictingSessionIdsAreRejected()
    {
        const string transcript = """
            {"type":"user","uuid":"user-1","sessionId":"session-a","message":{"role":"user","content":"start"}}
            {"type":"assistant","uuid":"assistant-1","sessionId":"session-b","message":{"role":"assistant","content":"done"}}
            """;

        var error = Assert.Throws<TrajectoryNormalizationException>(() =>
            TrajectoryConverter.NormalizeToIR(
                TrajectorySource.ClaudeCode,
                transcript));

        Assert.Equal(NormalizationErrorCode.SourceGroupConflict, error.Code);
    }

    [Fact]
    public void UnknownSemanticShapesProduceContentSafeDiagnostics()
    {
        const string transcript = """
            {"type":"future-record","secret":"DO-NOT-LEAK"}
            {"type":"user","uuid":"user-1","message":{"role":"user","content":"start"}}
            {"type":"assistant","uuid":"assistant-1","message":{"role":"assistant","content":[{"type":"future-block","secret":"DO-NOT-LEAK"},{"type":"text","text":"done"}]}}
            """;

        var trajectory = TrajectoryConverter.NormalizeToIR(
            TrajectorySource.ClaudeCode,
            transcript);

        Assert.Contains(
            trajectory.Diagnostics,
            static diagnostic =>
                diagnostic.Code == DiagnosticCodes.UnknownSemanticRecord);
        Assert.Contains(
            trajectory.Diagnostics,
            static diagnostic =>
                diagnostic.Code == DiagnosticCodes.UnknownContentBlock);
        Assert.All(
            trajectory.Diagnostics,
            static diagnostic => Assert.DoesNotContain(
                "DO-NOT-LEAK",
                diagnostic.Message,
                StringComparison.Ordinal));
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
