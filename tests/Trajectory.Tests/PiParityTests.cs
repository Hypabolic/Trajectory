using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class PiParityTests
{
    [Fact]
    public void PinnedPiFixtureMatchesUpstreamLettaTrajectoryV1()
    {
        var input = Fixture("Pi/tool-calls/input.jsonl");
        var expected = JsonNode.Parse(Fixture("Pi/tool-calls/expected.json"))!;
        var engine = TrajectoryEngine.CreateDefault();
        var normalizeInput = new NormalizeInput
        {
            Source = TrajectorySource.Pi,
            Transcript = input,
        };

        var result = engine.NormalizeTranscript(normalizeInput);
        var actualRecords = JsonNode.Parse(
            engine.NormalizeJson(normalizeInput, OutputSchemaIds.LettaTrajectoryV1))!;

        Assert.True(JsonNode.DeepEquals(expected["records"], actualRecords));
        Assert.Empty(result.Diagnostics);
        Assert.Equal("meta", actualRecords[0]!["role"]!.GetValue<string>());
        Assert.Equal("pi", actualRecords[0]!["source"]!.GetValue<string>());
    }

    [Fact]
    public void PiToolCallsAndResultsLinkByNativeId()
    {
        var trajectory = TrajectoryConverter.NormalizeToIR(Fixture("Pi/tool-calls/input.jsonl"));
        var calls = trajectory.Records.OfType<AssistantToolCallsIR>()
            .SelectMany(static record => record.ToolCalls)
            .ToDictionary(static call => call.Id, StringComparer.Ordinal);
        var results = trajectory.Records.OfType<ToolResultIR>().ToArray();

        Assert.Equal(2, calls.Count);
        Assert.All(results, result => Assert.True(calls.ContainsKey(result.ToolCallId)));
        Assert.Contains(results, static result => result.ToolCallId == "toolu_pi_1");
        Assert.Contains(results, static result => result.ToolCallId == "toolu_pi_2");
    }

    [Fact]
    public void PiProvenanceUsesActualUtf8LineOffsets()
    {
        var input = Fixture("Pi/tool-calls/input.jsonl");
        var expectedOffsets = NativeOffsets(input);
        var trajectory = TrajectoryConverter.NormalizeToIR(input);

        foreach (var record in trajectory.Records.Where(static record => record.Role != TrajectoryRole.Meta))
        {
            var nativeId = Assert.IsType<string>(record.Provenance.NativeRecordId);
            Assert.Equal(expectedOffsets[nativeId], record.Provenance.SourceOffset);
            Assert.Equal(SourceAnchorKind.Byte, record.Provenance.SourceAnchorKind);
            Assert.Equal(SourceIdentityKind.Native, record.Provenance.SourceIdentityKind);
        }
    }

    [Fact]
    public void MalformedJsonProducesContentSafeDiagnostic()
    {
        const string transcript = """
            {"type":"session","id":"safe","timestamp":"2026-01-01T00:00:00Z"}
            {"type":"message","id":"u1","timestamp":"2026-01-01T00:00:01Z","message":{"role":"user","content":"hello"}}
            {"secret":"PRIVATE-MARKER"
            {"type":"message","id":"a1","timestamp":"2026-01-01T00:00:02Z","message":{"role":"assistant","content":"done"}}
            """;

        var trajectory = TrajectoryConverter.NormalizeToIR(transcript);
        var diagnostic = Assert.Single(trajectory.Diagnostics);

        Assert.Equal(DiagnosticCodes.InvalidJsonLine, diagnostic.Code);
        Assert.DoesNotContain("PRIVATE-MARKER", diagnostic.Message, StringComparison.Ordinal);
    }

    private static Dictionary<string, long> NativeOffsets(string transcript)
    {
        var offsets = new Dictionary<string, long>(StringComparer.Ordinal);
        long byteOffset = 0;
        foreach (var line in transcript.Split('\n'))
        {
            if (!string.IsNullOrWhiteSpace(line))
            {
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;
                if (root.TryGetProperty("type", out var type) && type.GetString() == "message" &&
                    root.TryGetProperty("id", out var id))
                {
                    offsets[id.GetString()!] = byteOffset;
                }
            }

            byteOffset += Encoding.UTF8.GetByteCount(line) + 1L;
        }

        return offsets;
    }

    private static string Fixture(string relativePath) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", relativePath));
}
