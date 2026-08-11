using System.Text.Json;
using Json.Schema;
using Hypabolic.Trajectory.OpenTelemetry;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class ContractsAndConformanceTests
{
    private static readonly object SchemaLock = new();
    private static readonly Dictionary<string, JsonSchema> Schemas =
        new(StringComparer.Ordinal);

    [Fact]
    public void CompatibilityManifestIsValidAndMatchesRuntimeConstants()
    {
        var manifestText = File.ReadAllText(Path.Combine(
            AppContext.BaseDirectory,
            "Contracts",
            "compatibility.json"));
        using var manifest = JsonDocument.Parse(manifestText);
        AssertValid(
            "compatibility-manifest-v1.schema.json",
            manifest.RootElement);

        var root = manifest.RootElement;
        Assert.Equal(
            LettaCompatibilityVersion.Normalizer,
            root.GetProperty("contracts").GetProperty("normalizer").GetString());

        var schemaIds = root.GetProperty("public_schemas")
            .EnumerateArray()
            .Select(static item => item.GetProperty("id").GetString())
            .ToHashSet(StringComparer.Ordinal);
        Assert.Equal(
            new[]
            {
                OutputSchemaIds.HypabolicTrajectoryV1,
                OutputSchemaIds.JsonlMinimal,
                OutputSchemaIds.LettaCanonicalV1,
                OutputSchemaIds.LettaTrajectoryV1,
                OutputSchemaIds.OpenAiChatMessages,
                OutputSchemaIds.OtelGenAiSpansV1,
            }.Order(StringComparer.Ordinal),
            schemaIds.Order(StringComparer.Ordinal));

        var sources = root.GetProperty("implemented")
            .GetProperty("sources")
            .EnumerateArray()
            .Select(static item => item.GetString())
            .ToHashSet(StringComparer.Ordinal);
        Assert.Equal(
            new[] { "pi", "claude-code", "codex", "openclaw", "hermes", "ahp", "grok-build" }
                .Order(StringComparer.Ordinal),
            sources.Order(StringComparer.Ordinal));
        Assert.Equal("1.42.0", OtelGenAiConventions.Version);
    }

    [Fact]
    public void EverySharedCaseManifestIsValidAndReferencesCheckedInAssets()
    {
        var fixtureRoot = Path.Combine(AppContext.BaseDirectory, "Fixtures");
        var manifests = Directory.GetFiles(
            fixtureRoot,
            "case.json",
            SearchOption.AllDirectories);
        Assert.NotEmpty(manifests);

        foreach (var path in manifests)
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            AssertValid("conformance-case-v1.schema.json", document.RootElement);
            var root = document.RootElement;
            var directory = Path.GetDirectoryName(path)!;
            Assert.True(
                File.Exists(Path.Combine(directory, root.GetProperty("transcript").GetString()!)),
                $"{path} references a missing transcript.");
            foreach (var operation in root.GetProperty("operation").EnumerateObject())
            {
                var expected = operation.Value.GetProperty("expected").GetString()!;
                Assert.True(
                    File.Exists(Path.Combine(directory, expected)),
                    $"{path} references missing expected output {expected}.");
            }
        }
    }

    [Fact]
    public void ProtocolSchemasAreValidJsonSchemas()
    {
        foreach (var path in Directory.GetFiles(
                     Path.Combine(AppContext.BaseDirectory, "ConformanceProtocol"),
                     "*.json"))
        {
            var schema = JsonSchema.FromText(File.ReadAllText(path));
            Assert.NotNull(schema);
        }
    }

    private static void AssertValid(string schemaName, JsonElement instance)
    {
        lock (SchemaLock)
        {
            if (!Schemas.TryGetValue(schemaName, out var schema))
            {
                schema = JsonSchema.FromText(File.ReadAllText(Path.Combine(
                    AppContext.BaseDirectory,
                    "Schemas",
                    schemaName)));
                Schemas.Add(schemaName, schema);
            }

            var result = schema.Evaluate(instance);
            Assert.True(result.IsValid, $"{schemaName}: {result}");
        }
    }
}
