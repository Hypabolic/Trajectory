using System.Text.Json;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class HermesParityTests
{
    [Theory]
    [InlineData("hermes/tool-calls")]
    [InlineData("hermes/cleanup")]
    public void SharedFixturesMatchLettaAndCanonicalGoldens(string fixture)
    {
        var transcript = FixtureText($"{fixture}/input.json");
        var expectedLetta = FixtureText($"{fixture}/expected.letta.json");
        var expectedCanonical = FixtureText($"{fixture}/expected.canonical.json");

        var engine = TrajectoryEngine.CreateDefault();
        var ir = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.Hermes,
            Transcript = transcript,
        });

        Assert.Equal("hermes", ir.SourceName);
        Assert.DoesNotContain("pi", ir.SourceName, StringComparison.Ordinal);

        var letta = LettaEnvelope(
            engine.ProjectJson(ir, OutputSchemaIds.LettaTrajectoryV1),
            ir.Diagnostics);
        var canonical = engine.ProjectJson(ir, OutputSchemaIds.LettaCanonicalV1);

        AssertJsonEqual(expectedLetta, letta);
        AssertJsonEqual(expectedCanonical, canonical);
    }

    [Fact]
    public async Task MissingHermesStoreListsAsEmpty()
    {
        var missing = Path.Combine(
            Path.GetTempPath(),
            $"trajectory-hermes-missing-{Guid.NewGuid():N}");
        var page = await TrajectoryConverter.ListHermesTrajectoriesAsync(root: missing);
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

    private static string LettaEnvelope(
        string recordsJson,
        IReadOnlyList<TrajectoryDiagnostic> diagnostics)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream))
        {
            writer.WriteStartObject();
            writer.WritePropertyName("records");
            writer.WriteRawValue(recordsJson);
            writer.WriteStartArray("diagnostics");
            foreach (var diagnostic in diagnostics)
            {
                writer.WriteStartObject();
                writer.WriteString("code", diagnostic.Code);
                writer.WriteString("message", diagnostic.Message);
                if (diagnostic.InputLine is { } line)
                    writer.WriteNumber("inputLine", line);
                if (diagnostic.RecordIndex is { } recordIndex)
                    writer.WriteNumber("recordIndex", recordIndex);
                if (diagnostic.Count is { } count)
                    writer.WriteNumber("count", count);
                writer.WriteEndObject();
            }
            writer.WriteEndArray();
            writer.WriteEndObject();
        }

        return System.Text.Encoding.UTF8.GetString(stream.ToArray());
    }

    private static string FixtureText(string relativePath) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", relativePath));
}
