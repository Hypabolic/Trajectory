using System.Text.Json;
using Hypabolic.Trajectory;

var fixtures = new[]
{
    (TrajectorySource.Pi, "pi-tool-calls.jsonl"),
    (TrajectorySource.ClaudeCode, "claude-code-tool-call.jsonl"),
    (TrajectorySource.Codex, "codex-full.jsonl"),
};
var engine = TrajectoryEngine.CreateDefault();
foreach (var (source, fileName) in fixtures)
{
    var fixture = Path.Combine(AppContext.BaseDirectory, "Fixtures", fileName);
    var transcript = File.ReadAllText(fixture);
    var input = new NormalizeInput
    {
        Source = source,
        Transcript = transcript,
    };
    var letta = engine.NormalizeJson(input, OutputSchemaIds.LettaTrajectoryV1);
    var canonical = engine.NormalizeJson(input, OutputSchemaIds.LettaCanonicalV1);
    var hypabolic = engine.NormalizeJson(input, OutputSchemaIds.HypabolicTrajectoryV1);

    using var lettaDocument = JsonDocument.Parse(letta);
    using var canonicalDocument = JsonDocument.Parse(canonical);
    using var hypabolicDocument = JsonDocument.Parse(hypabolic);
    if (lettaDocument.RootElement.ValueKind != JsonValueKind.Array ||
        lettaDocument.RootElement.GetArrayLength() == 0 ||
        canonicalDocument.RootElement.GetProperty("records").GetArrayLength() == 0 ||
        hypabolicDocument.RootElement.ValueKind != JsonValueKind.Object ||
        hypabolicDocument.RootElement.GetProperty("schema_id").GetString() != "hypabolic-trajectory-v1")
    {
        return 1;
    }

    Console.WriteLine(
        $"AOT smoke normalized {lettaDocument.RootElement.GetArrayLength()} {source} records.");
}

return 0;
