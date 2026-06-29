using System.Diagnostics;
using System.Text.Json;
using Hypabolic.Trajectory;

const int iterations = 250;
var transcript = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", "input.jsonl"));
var input = new NormalizeInput
{
    Source = TrajectorySource.Pi,
    Transcript = transcript,
};
var engine = TrajectoryEngine.CreateDefault();

for (var index = 0; index < 10; index++)
{
    _ = engine.NormalizeToIR(input);
}

var allocatedBefore = GC.GetAllocatedBytesForCurrentThread();
var stopwatch = Stopwatch.StartNew();
TrajectoryIR? trajectory = null;
for (var index = 0; index < iterations; index++)
{
    trajectory = engine.NormalizeToIR(input);
}

stopwatch.Stop();
var allocated = GC.GetAllocatedBytesForCurrentThread() - allocatedBefore;
var outputs = new Dictionary<string, int>(StringComparer.Ordinal);
foreach (var schema in new[]
{
    OutputSchemaIds.LettaTrajectoryV1,
    OutputSchemaIds.LettaCanonicalV1,
    OutputSchemaIds.HypabolicTrajectoryV1,
    OutputSchemaIds.OpenAiChatMessages,
    OutputSchemaIds.JsonlMinimal,
})
{
    outputs[schema] = engine.NormalizeJson(input, schema).Length;
}

Console.WriteLine(JsonSerializer.Serialize(new
{
    runtime = "dotnet",
    iterations,
    elapsed_ms = stopwatch.Elapsed.TotalMilliseconds,
    operations_per_second = iterations / stopwatch.Elapsed.TotalSeconds,
    allocated_bytes = allocated,
    allocated_bytes_per_operation = allocated / iterations,
    input_bytes = System.Text.Encoding.UTF8.GetByteCount(transcript),
    records = trajectory!.Records.Count,
    output_characters = outputs,
}));
