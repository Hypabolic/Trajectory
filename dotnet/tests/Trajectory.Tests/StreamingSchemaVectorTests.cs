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

    [Fact]
    public void SharedStreamingSchemaFragmentsStayAligned()
    {
        // Schemas intentionally inline shared defs (offline validation without
        // cross-document $ref). Guard structural drift across the three docs.
        var stream = LoadSchemaNode("trajectory-stream-v1.schema.json");
        var delta = LoadSchemaNode("streaming-delta-v1.schema.json");
        var cursor = LoadSchemaNode("streaming-cursor-v1.schema.json");
        var caseSchema = LoadSchemaNode("streaming-case-v1.schema.json");
        var manifest = LoadSchemaNode("compatibility-manifest-v1.schema.json");

        var sdefs = stream["$defs"]!.AsObject();
        var ddefs = delta["$defs"]!.AsObject();
        var cdefs = cursor["$defs"]!.AsObject();

        foreach (var key in new[]
                 {
                     "bytePosition", "ahpServerSeqPosition", "snapshotRevisionPosition",
                     "hermesRowPosition", "sha256", "uint64", "int64", "nonNegativeInt64",
                     "position", "sourceName",
                 })
        {
            if (sdefs.ContainsKey(key) && cdefs.ContainsKey(key))
            {
                AssertJsonEqual(
                    $"cursor/{key} vs stream/{key}",
                    StripDocs(cdefs[key]!),
                    StripDocs(sdefs[key]!));
            }

            if (ddefs.ContainsKey(key) && cdefs.ContainsKey(key))
            {
                AssertJsonEqual(
                    $"cursor/{key} vs delta/{key}",
                    StripDocs(cdefs[key]!),
                    StripDocs(ddefs[key]!));
            }
        }

        foreach (var key in new[]
                 {
                     "streamDiagnostic", "streamRecordBody", "streamRecord",
                     "streamReset", "streamRevision", "recordStatus", "streamCursor",
                     "timestamp",
                 })
        {
            Assert.True(sdefs.ContainsKey(key), $"stream missing $defs.{key}");
            Assert.True(ddefs.ContainsKey(key), $"delta missing $defs.{key}");
            AssertJsonEqual(
                $"stream/{key} vs delta/{key}",
                StripDocs(sdefs[key]!),
                StripDocs(ddefs[key]!));
        }

        var cursorRoot = new JsonObject();
        foreach (var prop in cursor)
        {
            if (prop.Key is "$schema" or "$id" or "title" or "description" or "$defs")
            {
                continue;
            }

            cursorRoot[prop.Key] = prop.Value?.DeepClone();
        }

        if (cursorRoot["properties"] is JsonObject props)
        {
            props.Remove("$schema");
        }

        AssertJsonEqual(
            "streaming-cursor-v1 root vs streamCursor",
            StripDocs(cursorRoot),
            StripDocs(sdefs["streamCursor"]!));

        var caseCaps = caseSchema["$defs"]!["streamCapability"]!["enum"]!.AsArray()
            .Select(static n => n!.GetValue<string>())
            .ToArray();
        var manifestCaps = manifest["$defs"]!["capabilityName"]!["enum"]!.AsArray()
            .Select(static n => n!.GetValue<string>())
            .ToArray();
        Assert.Equal(manifestCaps, caseCaps);
    }

    [Fact]
    public void ProvisionalSnapshotVectorLocksWireShape()
    {
        var path = Path.Combine(VectorsRoot, "valid", "update-updated-provisional.json");
        Assert.True(File.Exists(path), path);
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        var instance = document.RootElement.GetProperty("instance");
        Assert.Equal("updated", instance.GetProperty("kind").GetString());
        var record = instance.GetProperty("snapshot").GetProperty("records")[0];
        Assert.Equal("provisional", record.GetProperty("status").GetString());
        Assert.Equal("prov-active-turn-1", record.GetProperty("provisional_id").GetString());
        var provisionalIds = instance.GetProperty("provisional").GetProperty("provisional_ids");
        Assert.Contains(
            provisionalIds.EnumerateArray(),
            static e => e.GetString() == "prov-active-turn-1");
        var result = Evaluate("trajectory-stream-v1.schema.json", instance);
        Assert.True(result.IsValid, result.ToString());
    }

    private static JsonObject LoadSchemaNode(string schemaName)
    {
        var text = File.ReadAllText(Path.Combine(SchemasRoot, schemaName));
        return JsonNode.Parse(text)!.AsObject();
    }

    private static JsonNode StripDocs(JsonNode node)
    {
        switch (node)
        {
            case JsonObject obj:
            {
                var copy = new JsonObject();
                foreach (var prop in obj)
                {
                    if (prop.Key is "description" or "title" or "$comment" or "comment")
                    {
                        continue;
                    }

                    if (prop.Value is not null)
                    {
                        copy[prop.Key] = StripDocs(prop.Value);
                    }
                    else
                    {
                        copy[prop.Key] = null;
                    }
                }

                return copy;
            }
            case JsonArray arr:
            {
                var copy = new JsonArray();
                foreach (var item in arr)
                {
                    copy.Add(item is null ? null : StripDocs(item));
                }

                return copy;
            }
            default:
                return node.DeepClone();
        }
    }

    private static void AssertJsonEqual(string label, JsonNode left, JsonNode right)
    {
        var a = left.ToJsonString(new JsonSerializerOptions { WriteIndented = false });
        var b = right.ToJsonString(new JsonSerializerOptions { WriteIndented = false });
        // Normalize property order via parse/serialize round-trip with sorted keys.
        a = CanonicalJson(a);
        b = CanonicalJson(b);
        Assert.True(a == b, $"schema fragment drift ({label})");
    }

    private static string CanonicalJson(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return CanonicalElement(doc.RootElement);
    }

    private static string CanonicalElement(JsonElement el)
    {
        switch (el.ValueKind)
        {
            case JsonValueKind.Object:
            {
                var parts = el.EnumerateObject()
                    .OrderBy(static p => p.Name, StringComparer.Ordinal)
                    .Select(static p =>
                        $"{JsonSerializer.Serialize(p.Name)}:{CanonicalElement(p.Value)}");
                return "{" + string.Join(",", parts) + "}";
            }
            case JsonValueKind.Array:
            {
                var parts = el.EnumerateArray().Select(CanonicalElement);
                return "[" + string.Join(",", parts) + "]";
            }
            case JsonValueKind.String:
                return JsonSerializer.Serialize(el.GetString());
            case JsonValueKind.Number:
                return el.GetRawText();
            case JsonValueKind.True:
                return "true";
            case JsonValueKind.False:
                return "false";
            case JsonValueKind.Null:
                return "null";
            default:
                return el.GetRawText();
        }
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
