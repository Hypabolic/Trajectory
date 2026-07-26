using System.Text.Json;
using System.Text.Json.Nodes;
using Json.Schema;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class HypabolicProjectionTests
{
    [Fact]
    public void HypabolicProjectionValidatesAgainstCheckedInSchema()
    {
        var input = Fixture("pi/tool-calls/input.jsonl");
        var json = TrajectoryConverter.NormalizeJson(
            input,
            OutputSchemaIds.HypabolicTrajectoryV1);
        var schema = JsonSchema.FromText(
            File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Schemas", "hypabolic-trajectory-v1.schema.json")));
        using var document = JsonDocument.Parse(json);
        var evaluation = schema.Evaluate(document.RootElement);
        var instance = JsonNode.Parse(json)!;

        Assert.True(evaluation.IsValid, evaluation.ToString());
        Assert.Equal("hypabolic-trajectory-v1", instance["schema_id"]!.GetValue<string>());
        Assert.Equal("pi", instance["source"]!["type"]!.GetValue<string>());
        Assert.NotEmpty(instance["records"]!.AsArray());
    }

    [Fact]
    public void HypabolicIdentityIsDeterministicForTheSamePiSession()
    {
        var input = Fixture("pi/tool-calls/input.jsonl");
        var left = TrajectoryConverter.NormalizeToHypabolic(input);
        var right = TrajectoryConverter.NormalizeToHypabolic(input);

        Assert.Equal(left.TrajectoryId, right.TrajectoryId);
        Assert.Equal(
            left.Records.Select(static record => record.Id),
            right.Records.Select(static record => record.Id));
    }

    private static string Fixture(string relativePath) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", relativePath));
}
