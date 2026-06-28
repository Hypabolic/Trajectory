using System.Text.Json;
using Json.Schema;
using Hypabolic.Trajectory.OpenTelemetry;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class ContractsAndConformanceTests
{
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
        Assert.Equal(
            "f165ecf0af35da40512a288c4380a36b3102403c",
            root.GetProperty("upstream").GetProperty("commit").GetString());

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
        var schema = JsonSchema.FromText(File.ReadAllText(Path.Combine(
            AppContext.BaseDirectory,
            "Schemas",
            schemaName)));
        var result = schema.Evaluate(instance);
        Assert.True(result.IsValid, $"{schemaName}: {result}");
    }
}
