using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Json.Schema;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class PiSlice2ParityTests
{
    [Fact]
    public void CanonicalProjectionMatchesPinnedUpstreamGolden()
    {
        var input = Fixture("Pi/tool-calls/input.jsonl");
        var expected = JsonNode.Parse(
            Fixture("Pi/tool-calls/canonical-expected.json"))!;
        var actual = JsonNode.Parse(TrajectoryConverter.NormalizeJson(
            input,
            OutputSchemaIds.LettaCanonicalV1))!;

        Assert.True(JsonNode.DeepEquals(expected, actual));

        var schema = JsonSchema.FromText(File.ReadAllText(Path.Combine(
            AppContext.BaseDirectory,
            "Schemas",
            "letta-canonical-v1.schema.json")));
        using var records = JsonDocument.Parse(actual["records"]!.ToJsonString());
        var evaluation = schema.Evaluate(records.RootElement);
        Assert.True(evaluation.IsValid, evaluation.ToString());
    }

    [Fact]
    public void DuplicateCallsAndReverseArrivalResultsLinkDeterministically()
    {
        const string transcript = """
            {"type":"session","id":"duplicates","timestamp":"2026-01-01T00:00:00Z"}
            {"type":"message","id":"u","timestamp":"2026-01-01T00:00:01Z","message":{"role":"user","content":"run both"}}
            {"type":"message","id":"r1","timestamp":"2026-01-01T00:00:02Z","message":{"role":"toolResult","toolCallId":"same","content":"first"}}
            {"type":"message","id":"a","timestamp":"2026-01-01T00:00:03Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"same","name":"one","arguments":{}},{"type":"toolCall","id":"same","name":"two","arguments":{}}]}}
            {"type":"message","id":"r2","timestamp":"2026-01-01T00:00:04Z","message":{"role":"toolResult","toolCallId":"same","content":"second"}}
            """;

        var trajectory = TrajectoryConverter.NormalizeToIR(transcript);
        var calls = trajectory.Records.OfType<AssistantToolCallsIR>()
            .SelectMany(static item => item.ToolCalls)
            .Select(static item => item.Id)
            .ToArray();
        var results = trajectory.Records.OfType<ToolResultIR>()
            .Select(static item => item.ToolCallId)
            .ToArray();

        Assert.Equal(new[] { "same", "same__2" }, calls);
        Assert.Equal(new[] { "same", "same__2" }, results);
        Assert.Contains(
            trajectory.Diagnostics,
            static diagnostic => diagnostic.Code == DiagnosticCodes.DuplicateToolCallId);
    }

    [Fact]
    public void MissingCallIdAndInvalidArgumentsAreRepaired()
    {
        const string transcript = """
            {"type":"session","id":"repair","timestamp":"2026-01-01T00:00:00Z"}
            {"type":"message","id":"u","timestamp":"2026-01-01T00:00:01Z","message":{"role":"user","content":"run"}}
            {"type":"message","id":"a","timestamp":"2026-01-01T00:00:02Z","message":{"role":"assistant","content":[{"type":"toolCall","name":"bash","arguments":"not-json"}]}}
            """;

        var trajectory = TrajectoryConverter.NormalizeToIR(transcript);
        var call = Assert.Single(
            Assert.Single(trajectory.Records.OfType<AssistantToolCallsIR>()).ToolCalls);
        using var arguments = JsonDocument.Parse(call.ArgumentsJson);

        Assert.Equal("call_2", call.Id);
        Assert.Equal(JsonValueKind.Object, arguments.RootElement.ValueKind);
        Assert.Equal("not-json", arguments.RootElement.GetProperty("_raw").GetString());
        Assert.Contains(
            trajectory.Diagnostics,
            static diagnostic => diagnostic.Code == DiagnosticCodes.ToolCallIdSynthesized);
        Assert.Contains(
            trajectory.Diagnostics,
            static diagnostic => diagnostic.Code == DiagnosticCodes.ToolArgumentsReshaped);
    }

    [Fact]
    public void BoundsUseUnicodeCodePointsAndIncludeTheMarker()
    {
        var argumentValue = string.Concat(Enumerable.Repeat("😀", 120));
        var resultValue = string.Concat(Enumerable.Repeat("🌊", 120));
        var transcript = $$"""
            {"type":"session","id":"bounds","timestamp":"2026-01-01T00:00:00Z"}
            {"type":"message","id":"u","timestamp":"2026-01-01T00:00:01Z","message":{"role":"user","content":"run"}}
            {"type":"message","id":"a","timestamp":"2026-01-01T00:00:02Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"bounded","name":"bash","arguments":{"value":"{{argumentValue}}"}}]}}
            {"type":"message","id":"r","timestamp":"2026-01-01T00:00:03Z","message":{"role":"toolResult","toolCallId":"bounded","content":"{{resultValue}}"}}
            """;
        var options = new NormalizeOptions
        {
            Bounds = new NormalizationBounds
            {
                ToolArguments = new ToolArgumentBounds { MaxCharacters = 64 },
                ToolResults = new ToolResultBounds
                {
                    MaxCharacters = 40,
                    Strategy = ToolResultTruncationStrategy.HeadTail,
                },
            },
        };

        var trajectory = TrajectoryConverter.NormalizeToIR(transcript, options);
        var arguments = Assert.Single(
            Assert.Single(trajectory.Records.OfType<AssistantToolCallsIR>()).ToolCalls)
            .ArgumentsJson;
        var result = Assert.Single(trajectory.Records.OfType<ToolResultIR>()).Content;

        Assert.True(CodePoints(arguments) <= 64);
        Assert.True(CodePoints(result) <= 40);
        Assert.Contains("[truncated,", arguments, StringComparison.Ordinal);
        Assert.Contains("[truncated,", result, StringComparison.Ordinal);
        Assert.StartsWith("🌊", result);
        Assert.EndsWith("🌊", result);
        Assert.Contains(
            trajectory.Diagnostics,
            static diagnostic => diagnostic.Code == DiagnosticCodes.ToolArgumentsTruncated);
        Assert.Contains(
            trajectory.Diagnostics,
            static diagnostic => diagnostic.Code == DiagnosticCodes.ToolResultTruncated);
    }

    [Fact]
    public void ToolResultOmissionLeavesCallsAndResolvedConfig()
    {
        var options = new NormalizeOptions
        {
            Filters = new NormalizationFilters
            {
                ToolResults = ToolResultPolicy.Omit,
            },
        };

        var trajectory = TrajectoryConverter.NormalizeToIR(
            Fixture("Pi/tool-calls/input.jsonl"),
            options);

        Assert.NotEmpty(trajectory.Records.OfType<AssistantToolCallsIR>());
        Assert.Empty(trajectory.Records.OfType<ToolResultIR>());
        Assert.Equal(
            AppliedNormalizationConfig.DefaultToolArgumentCharacters,
            trajectory.Config.Bounds.ToolArguments.MaxCharacters);
        Assert.Equal(
            AppliedNormalizationConfig.DefaultToolResultCharacters,
            trajectory.Config.Bounds.ToolResults.MaxCharacters);
    }

    [Fact]
    public void PartialContinuationKeepsCrossChunkResultAndOmitsCanonicalMeta()
    {
        const string transcript = """
            {"type":"message","id":"r","timestamp":"2026-01-01T00:00:03Z","message":{"role":"toolResult","toolCallId":"earlier","content":"done"}}
            """;
        var context = new SourceContext
        {
            GroupId = "partial",
            BaseByteOffset = 512,
            Partial = true,
        };

        var trajectory = TrajectoryConverter.NormalizeToIR(
            transcript,
            sourceContext: context);
        var canonical = TrajectoryConverter.NormalizeToCanonical(
            transcript,
            sourceContext: context);

        Assert.Equal("earlier", Assert.Single(
            trajectory.Records.OfType<ToolResultIR>()).ToolCallId);
        Assert.DoesNotContain(
            canonical.Records,
            static record => record.RecordType == "meta");
    }

    [Fact]
    public void OffsetZeroPartialStillEmitsCanonicalMeta()
    {
        const string transcript = """
            {"type":"message","id":"u","timestamp":"2026-01-01T00:00:01Z","message":{"role":"user","content":"first chunk"}}
            """;

        var canonical = TrajectoryConverter.NormalizeToCanonical(
            transcript,
            sourceContext: new SourceContext
            {
                GroupId = "partial-zero",
                BaseByteOffset = 0,
                Partial = true,
            });

        Assert.Equal("meta", canonical.Records[0].RecordType);
    }

    [Fact]
    public void TimestampsInterpolateAndSynthesizeWithPinnedPolicy()
    {
        const string interpolatedTranscript = """
            {"type":"session","id":"time","timestamp":"2026-01-01T00:00:00Z"}
            {"type":"message","id":"u","timestamp":"2026-01-01T00:00:00Z","message":{"role":"user","content":"hello"}}
            {"type":"message","id":"a1","message":{"role":"assistant","content":"middle"}}
            {"type":"message","id":"a2","timestamp":"2026-01-01T00:00:10Z","message":{"role":"assistant","content":"done"}}
            """;
        const string synthesizedTranscript = """
            {"type":"session","id":"synth"}
            {"type":"message","id":"u","message":{"role":"user","content":"hello"}}
            {"type":"message","id":"a","message":{"role":"assistant","content":"done"}}
            """;

        var interpolated = TrajectoryConverter.NormalizeToIR(interpolatedTranscript);
        var synthesized = TrajectoryConverter.NormalizeToIR(synthesizedTranscript);
        var middle = interpolated.Records.OfType<MessageIR>()
            .Single(static record => record.Content == "middle");
        var synthesizedMessages = synthesized.Records.OfType<MessageIR>().ToArray();

        Assert.Equal(
            DateTimeOffset.Parse("2026-01-01T00:00:05Z"),
            middle.Timestamp);
        Assert.Contains(
            interpolated.Diagnostics,
            static diagnostic => diagnostic.Code == DiagnosticCodes.TimestampsInterpolated);
        Assert.Equal(
            DateTimeOffset.Parse("2026-01-01T00:00:00Z"),
            synthesizedMessages[0].Timestamp);
        Assert.Equal(
            DateTimeOffset.Parse("2026-01-01T00:00:15Z"),
            synthesizedMessages[1].Timestamp);
        Assert.Contains(
            synthesized.Diagnostics,
            static diagnostic => diagnostic.Code == DiagnosticCodes.TimestampsSynthesized);
    }

    [Fact]
    public void CanonicalIdentitySurvivesAppendAndChunkOffsetOnlyAffectsByteAnchors()
    {
        var original = Fixture("Pi/tool-calls/input.jsonl");
        var appended = original + """
            {"type":"message","id":"extra","timestamp":"2026-07-24T06:21:04Z","message":{"role":"assistant","content":"extra"}}

            """;
        var before = TrajectoryConverter.NormalizeToCanonical(original);
        var after = TrajectoryConverter.NormalizeToCanonical(appended);

        Assert.Equal(
            before.Records.Select(static record => record.RecordId),
            after.Records.Take(before.Records.Count).Select(static record => record.RecordId));
        Assert.Equal(
            before.Records.Select(static record => record.SourceOrderId),
            after.Records.Take(before.Records.Count).Select(static record => record.SourceOrderId));
    }

    [Fact]
    public void ConflictingSourceGroupIsRejected()
    {
        var exception = Assert.Throws<TrajectoryNormalizationException>(() =>
            TrajectoryConverter.NormalizeToIR(
                Fixture("Pi/tool-calls/input.jsonl"),
                sourceContext: new SourceContext { GroupId = "wrong" }));

        Assert.Equal(NormalizationErrorCode.SourceGroupConflict, exception.Code);
    }

    [Fact]
    public void PiInvocationMetadataRoundTripsWithoutHeuristics()
    {
        var trajectory = TrajectoryConverter.NormalizeToIR(
            Fixture("Pi/tool-calls/input.jsonl"));
        var invocations = trajectory.Execution.ModelInvocations;

        Assert.Equal(3, invocations.Count);
        var first = invocations[0];
        Assert.Equal("anthropic", first.Provider);
        Assert.Equal("anthropic-messages", first.ApiFamily);
        Assert.Equal("claude-sonnet-5", first.RequestedModel);
        Assert.Equal("claude-sonnet-5", first.ResponseModel);
        Assert.Equal("msg_uxf4ro49", first.ResponseId);
        Assert.Equal("toolUse", first.StopReason);
        Assert.Equal(120L, first.Usage!.InputTokens);
        Assert.Equal(60L, first.Usage.OutputTokens);
        Assert.Equal(180L, first.Usage.TotalTokens);
        Assert.Null(first.StartedAt);
        Assert.Equal(
            DateTimeOffset.FromUnixTimeMilliseconds(1784874063561),
            first.CompletedAt);
    }

    [Fact]
    public void BoundsRejectImpossibleArgumentObjectLimit()
    {
        var exception = Assert.Throws<TrajectoryNormalizationException>(() =>
            TrajectoryConverter.NormalizeToIR(
                Fixture("Pi/tool-calls/input.jsonl"),
                new NormalizeOptions
                {
                    Bounds = new NormalizationBounds
                    {
                        ToolArguments = new ToolArgumentBounds { MaxCharacters = 1 },
                    },
                }));

        Assert.Equal(NormalizationErrorCode.InvalidInput, exception.Code);
    }

    private static int CodePoints(string value) => value.EnumerateRunes().Count();

    private static string Fixture(string relativePath) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", relativePath));
}
