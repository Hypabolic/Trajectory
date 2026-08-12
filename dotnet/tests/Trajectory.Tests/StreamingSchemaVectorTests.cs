using System.Text.Json;
using System.Text.Json.Nodes;
using Json.Schema;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

/// <summary>
/// LS-01: schema valid/invalid vectors for trajectory-stream-v1 contracts.
/// Does not exercise stream engines (those land in later slices).
/// </summary>
public sealed class StreamingSchemaVectorTests
{
    private static readonly object SchemaLock = new();
    private static readonly Dictionary<string, JsonSchema> Schemas =
        new(StringComparer.Ordinal);

    private static readonly string[] PrivacySentinels =
    [
        "SECRET_TOKEN_xyz",
        "/Users/real-user/",
        "auth.json",
    ];

    private static string VectorsRoot =>
        Path.Combine(AppContext.BaseDirectory, "ContractVectors", "streaming");

    private static string SchemasRoot =>
        Path.Combine(AppContext.BaseDirectory, "Schemas");

    [Fact]
    public void StreamingSchemasAreValidJsonSchemas()
    {
        foreach (var name in new[]
                 {
                     "trajectory-stream-v1.schema.json",
                     "streaming-cursor-v1.schema.json",
                     "streaming-delta-v1.schema.json",
                     "streaming-case-v1.schema.json",
                     "compatibility-manifest-v1.schema.json",
                 })
        {
            var path = Path.Combine(SchemasRoot, name);
            Assert.True(File.Exists(path), $"missing schema {path}");
            var schema = LoadSchema(name);
            Assert.NotNull(schema);
        }
    }

    [Fact]
    public void ValidStreamingVectorsPassTheirSchemas()
    {
        var validDir = Path.Combine(VectorsRoot, "valid");
        Assert.True(Directory.Exists(validDir), $"missing {validDir}");
        var files = Directory.GetFiles(validDir, "*.json");
        Assert.NotEmpty(files);

        foreach (var path in files.Order(StringComparer.Ordinal))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            var schemaName = root.GetProperty("schema").GetString()!;
            var instance = root.GetProperty("instance");
            var result = Evaluate(schemaName, instance);
            Assert.True(
                result.IsValid,
                $"{Path.GetFileName(path)} should be valid against {schemaName}: {result}");
        }
    }

    [Fact]
    public void InvalidStreamingVectorsFailTheirSchemas()
    {
        var invalidDir = Path.Combine(VectorsRoot, "invalid");
        Assert.True(Directory.Exists(invalidDir), $"missing {invalidDir}");
        var files = Directory.GetFiles(invalidDir, "*.json");
        Assert.NotEmpty(files);

        foreach (var path in files.Order(StringComparer.Ordinal))
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            var schemaName = root.GetProperty("schema").GetString()!;
            var instance = root.GetProperty("instance");
            var result = Evaluate(schemaName, instance);
            Assert.False(
                result.IsValid,
                $"{Path.GetFileName(path)} should be invalid against {schemaName}");
        }
    }

    [Fact]
    public void ValidVectorDiagnosticsAreContentSafe()
    {
        var validDir = Path.Combine(VectorsRoot, "valid");
        foreach (var path in Directory.GetFiles(validDir, "*.json"))
        {
            var text = File.ReadAllText(path);
            // Privacy-negative fixtures live under invalid/; valid vectors must
            // not embed sentinel secrets or real-home path prefixes.
            foreach (var sentinel in PrivacySentinels)
            {
                // Forbidden substrings listed as privacy test expectations are OK
                // only inside the privacy.forbidden_substrings array of case vectors.
                if (path.EndsWith("case-minimal-sequence.json", StringComparison.Ordinal)
                    && text.Contains("\"forbidden_substrings\"", StringComparison.Ordinal))
                {
                    continue;
                }

                Assert.DoesNotContain(sentinel, text, StringComparison.Ordinal);
            }

            using var document = JsonDocument.Parse(text);
            AssertNoForbiddenDiagnosticFields(document.RootElement);
        }
    }

    [Fact]
    public void CompatibilityManifestStillValidWithoutStreamCapsClaimed()
    {
        var manifestText = File.ReadAllText(Path.Combine(
            AppContext.BaseDirectory,
            "Contracts",
            "compatibility.json"));
        using var manifest = JsonDocument.Parse(manifestText);
        var result = Evaluate(
            "compatibility-manifest-v1.schema.json",
            manifest.RootElement);
        Assert.True(result.IsValid, result.ToString());

        var required = manifest.RootElement
            .GetProperty("capabilities")
            .GetProperty("required")
            .EnumerateArray()
            .Select(static e => e.GetString()!)
            .ToArray();
        var optional = manifest.RootElement
            .GetProperty("capabilities")
            .GetProperty("optional")
            .EnumerateArray()
            .Select(static e => e.GetString()!)
            .ToArray();
        Assert.DoesNotContain(required, static c => c.StartsWith("stream-", StringComparison.Ordinal));
        Assert.DoesNotContain(optional, static c => c.StartsWith("stream-", StringComparison.Ordinal));
    }

    [Fact]
    public void EmptySnapshotAllowsZeroRecords()
    {
        var path = Path.Combine(VectorsRoot, "valid", "snapshot-empty.json");
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        var records = document.RootElement
            .GetProperty("instance")
            .GetProperty("records");
        Assert.Equal(JsonValueKind.Array, records.ValueKind);
        Assert.Empty(records.EnumerateArray());
        var result = Evaluate(
            "trajectory-stream-v1.schema.json",
            document.RootElement.GetProperty("instance"));
        Assert.True(result.IsValid, result.ToString());
    }

    private static EvaluationResults Evaluate(string schemaName, JsonElement instance)
    {
        var schema = LoadSchema(schemaName);
        return schema.Evaluate(
            instance,
            new EvaluationOptions { OutputFormat = OutputFormat.List });
    }

    /// <summary>
    /// Load a schema without registering its published <c>$id</c> on the process-global
    /// Json.Schema registry (other test classes already register the same contracts).
    /// </summary>
    private static JsonSchema LoadSchema(string schemaName)
    {
        lock (SchemaLock)
        {
            if (Schemas.TryGetValue(schemaName, out var cached))
            {
                return cached;
            }

            var text = File.ReadAllText(Path.Combine(SchemasRoot, schemaName));
            var node = JsonNode.Parse(text)!.AsObject();
            node.Remove("$id");
            var schema = JsonSchema.FromText(node.ToJsonString());
            Schemas.Add(schemaName, schema);
            return schema;
        }
    }

    private static void AssertNoForbiddenDiagnosticFields(JsonElement element)
    {
        if (element.ValueKind == JsonValueKind.Object)
        {
            foreach (var prop in element.EnumerateObject())
            {
                if (prop.Name is "path" or "raw_line" or "rawLine" or "file_path" or "secret")
                {
                    // Only forbid these on diagnostic-like objects (have code+message).
                    if (element.TryGetProperty("code", out _)
                        && element.TryGetProperty("message", out _))
                    {
                        Assert.Fail(
                            $"diagnostic object must not contain property '{prop.Name}'");
                    }
                }

                AssertNoForbiddenDiagnosticFields(prop.Value);
            }
        }
        else if (element.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in element.EnumerateArray())
            {
                AssertNoForbiddenDiagnosticFields(item);
            }
        }
    }
}
