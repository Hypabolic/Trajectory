using System.Text.Json;
using Trajectory;
using Trajectory.Adapters.Letta;
using Trajectory.Adapters.Pi;

var tests = new (string Name, Action Run)[]
{
    ("SHA-256 identity", IdentityIsStable),
    ("Synthesized IDs", SynthesizedIdsAreDeterministic),
    ("Pi structures", PiStructuresAreSupported),
    ("Safe diagnostics", DiagnosticsDoNotLeakInput),
    ("Engine registration", EnginePipelineWorks),
    ("Exact contract options", ExactContractOptionsWork),
    ("Projection controls", ProjectionControlsWork),
    ("Generated IR serialization", GeneratedIrSerializationWorks),
    ("Golden end-to-end", GoldenFixtureMatches)
};

var failed = 0;
foreach (var test in tests)
{
    try
    {
        test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception exception)
    {
        failed++;
        Console.Error.WriteLine($"FAIL {test.Name}: {exception}");
    }
}

Console.WriteLine($"{tests.Length - failed}/{tests.Length} tests passed.");
return failed == 0 ? 0 : 1;

static void IdentityIsStable()
{
    Equal(
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        DeterministicIdentity.Sha256Hex("hello"));
    Equal(
        DeterministicIdentity.Create("id", "a", "bc"),
        DeterministicIdentity.Create("id", "a", "bc"));
    NotEqual(
        DeterministicIdentity.Create("id", "a", "bc"),
        DeterministicIdentity.Create("id", "ab", "c"));
}

static void SynthesizedIdsAreDeterministic()
{
    const string first = """
        {"type":"session","id":"stable"}
        {"type":"message","message":{"role":"assistant","content":[{"type":"toolCall","name":"search","arguments":{"z":2,"a":1}}]}}
        """;
    const string second = """
        { "type": "session", "id": "stable" }
        {"message":{"content":[{"arguments":{"a":1,"z":2},"name":"search","type":"toolCall"}],"role":"assistant"},"type":"message"}
        """;

    var left = TrajectoryConverter.NormalizeToIR(first);
    var right = TrajectoryConverter.NormalizeToIR(second);
    var leftAssistant = left.Records.OfType<AssistantToolCallsIR>().Single();
    var rightAssistant = right.Records.OfType<AssistantToolCallsIR>().Single();
    Equal(leftAssistant.Id, rightAssistant.Id);
    Equal(leftAssistant.ToolCalls[0].Id, rightAssistant.ToolCalls[0].Id);
    Equal("{\"a\":1,\"z\":2}", leftAssistant.ToolCalls[0].ArgumentsJson);
}

static void PiStructuresAreSupported()
{
    const string source = """
        {"session":{"id":"nested-session","messages":[{"sender":"human","text":"hello"},{"author":{"role":"assistant"},"content":"hi","tool_calls":[{"function":{"name":"lookup","arguments":"{\"q\":\"x\"}"}},{"function":{"name":"lookup","arguments":"{\"q\":\"x\"}"}}]}]}}
        [{"role":"tool","tool_call_id":"generated-call","name":"lookup","content":"done"}]
        """;

    ISourceAdapter adapter = new PiJsonlSourceAdapter();
    Equal(TrajectorySource.Pi, adapter.Source);
    var result = adapter.Parse(
        source,
        new SourceContext("context-session"),
        new NormalizationOptions());
    False(result.HasErrors);
    Equal(4, result.Records.Count);
    True(result.Records[0] is MetaIR);
    True(result.Records[1] is MessageIR);
    True(result.Records[2] is AssistantToolCallsIR);
    True(result.Records[3] is ToolResultIR);

    var meta = (MetaIR)result.Records[0];
    var user = (MessageIR)result.Records[1];
    var assistant = (AssistantToolCallsIR)result.Records[2];
    var toolResult = (ToolResultIR)result.Records[3];
    Equal(TrajectoryRoles.User, user.Role);
    Equal("lookup", assistant.ToolCalls[0].Name);
    NotEqual(
        assistant.ToolCalls[0].Id,
        assistant.ToolCalls[1].Id);
    Equal("generated-call", toolResult.ToolCallId);
    Equal("context-session", result.GroupId);
    Equal("pi-jsonl", result.Source);
    Equal("pi-jsonl", meta.SourceName);
    False(typeof(IRRecord).IsAssignableFrom(typeof(ToolCallIR)));
    Equal(new[] { 0, 1, 2 }, result.Records.Skip(1).Select(static record => record.Order).ToArray());
}

static void DiagnosticsDoNotLeakInput()
{
    const string secretInput = "{\"content\":\"PRIVATE-MARKER\"";
    var result = new PiJsonlSourceAdapter().Parse(
        secretInput,
        null,
        new NormalizationOptions { Strict = true });
    True(result.HasErrors);
    True(result.Diagnostics.Count == 1);
    False(result.Diagnostics[0].Message.Contains("PRIVATE-MARKER", StringComparison.Ordinal));
    False(result.Diagnostics[0].ToString().Contains(secretInput, StringComparison.Ordinal));
}

static void EnginePipelineWorks()
{
    var engine = new TrajectoryEngine()
        .AddSourceAdapter(new PiJsonlSourceAdapter())
        .AddOutputAdapter(new LettaTrajectoryV1OutputAdapter());
    var input = new NormalizeInput(
        TrajectorySource.Pi,
        """{"role":"user","content":"hello"}""",
        new SourceContext("engine-test", 10, true));
    var trajectory = engine.NormalizeToIR(input);
    True(trajectory.Records[0] is MetaIR);
    True(trajectory.Records[1] is MessageIR);

    var projected = engine.Project(
        trajectory,
        "letta-trajectory-v1",
        new ProjectionOptions { AppendFinalNewline = false });
    var output = engine.Normalize(
        TrajectorySource.Pi,
        input.Transcript,
        "letta-trajectory-v1",
        input.Context,
        projectionOptions: new ProjectionOptions { AppendFinalNewline = false });

    using var document = JsonDocument.Parse(output);
    Equal("letta-trajectory-v1", document.RootElement.GetProperty("format").GetString());
    Equal("user", document.RootElement.GetProperty("messages")[0].GetProperty("role").GetString());
    Equal(projected, output);

    var optionsContext = new SourceContext("options-context", 25, true);
    var optionsTrajectory = engine.NormalizeToIR(new NormalizeInput(
        TrajectorySource.Pi,
        """{"role":"user","content":"from options"}""",
        Options: new NormalizationOptions { SourceContext = optionsContext }));
    Equal("options-context", optionsTrajectory.GroupId);
    Equal(optionsContext, optionsTrajectory.Config.SourceContext);
}

static void ExactContractOptionsWork()
{
    Equal(
        new[]
        {
            "Pi", "ClaudeCode", "Codex", "LettaCode",
            "OpenClaw", "OpenHands", "Hermes", "DeepAgents"
        },
        Enum.GetNames<TrajectorySource>());
    Equal(typeof(long?), typeof(SourceContext).GetProperty("BaseByteOffset")!.PropertyType);
    Equal(typeof(int), typeof(IRRecord).GetProperty("Order")!.PropertyType);
    Equal(typeof(string), typeof(MessageIR).GetProperty("Content")!.PropertyType);
    Equal(
        ToolResultTruncationStrategy.HeadTail,
        new ToolResultBounds().Strategy);
    var context = new SourceContext("contract-group", 40, true);
    var options = new NormalizationOptions
    {
        SourceContext = context,
        Filters = new NormalizationFilters(),
        Bounds = new NormalizationBounds
        {
            ToolArguments = new ToolArgumentBounds
            {
                MaxBytes = 4,
                Truncation = TruncationMode.Head
            },
            ToolResults = new ToolResultBounds
            {
                MaxCharacters = 4,
                Strategy = ToolResultTruncationStrategy.HeadTail
            }
        }
    };
    var trajectory = new PiJsonlSourceAdapter().Parse(
        """
        {"role":"assistant","tool_calls":[{"id":"c","function":{"name":"run","arguments":"123456"}}]}
        {"role":"tool","tool_call_id":"c","content":"abcdef"}
        """,
        null,
        options);

    Equal("contract-group", trajectory.GroupId);
    Equal(0, trajectory.Records[1].Order);
    Equal("1234", ((AssistantToolCallsIR)trajectory.Records[1]).ToolCalls[0].ArgumentsJson);
    Equal("abef", ((ToolResultIR)trajectory.Records[2]).Content);
    Equal(context, trajectory.Config.SourceContext);
    Equal(new NormalizationFilters(), trajectory.Config.Filters);
    Equal(TruncationMode.Head, trajectory.Config.Bounds!.ToolArguments!.Truncation);
    Equal(
        ToolResultTruncationStrategy.HeadTail,
        trajectory.Config.Bounds.ToolResults!.Strategy);
}

static void ProjectionControlsWork()
{
    IOutputSchemaAdapter adapter = new LettaTrajectoryV1OutputAdapter();
    Equal("letta-trajectory-v1", adapter.SchemaId);
    Equal("1", adapter.SchemaVersion);

    var trajectory = new PiJsonlSourceAdapter().Parse(
        """
        {"role":"user","content":"hello","timestamp":"2026-01-01T00:00:00Z"}
        {"role":"tool","tool_call_id":"c","content":"result"}
        """,
        null,
        new NormalizationOptions());
    var output = adapter.Project(
        trajectory,
        new OutputProjectionOptions
        {
            IncludeDiagnostics = true,
            IncludeTimestamps = false,
            OmitToolResults = true,
            AppendFinalNewline = false
        });

    using var document = JsonDocument.Parse(output);
    var root = document.RootElement;
    Equal(1, root.GetProperty("messages").GetArrayLength());
    False(root.GetProperty("messages")[0].TryGetProperty("timestamp", out _));
    True(root.TryGetProperty("diagnostics", out _));
}

static void GoldenFixtureMatches()
{
    var fixtureDirectory = Path.Combine(AppContext.BaseDirectory, "Fixtures");
    var input = File.ReadAllText(Path.Combine(fixtureDirectory, "pi-session.jsonl"));
    var expected = File.ReadAllText(Path.Combine(fixtureDirectory, "letta-trajectory-v1.jsonl"))
        .ReplaceLineEndings("\n");
    var actual = TrajectoryConverter.Normalize(input).ReplaceLineEndings("\n");
    Equal(expected, actual);
}

static void GeneratedIrSerializationWorks()
{
    var trajectory = TrajectoryConverter.NormalizeToIR(
        """{"role":"assistant","content":"ok","tool_calls":[{"id":"c1","function":{"name":"run","arguments":"{}"}}]}""");
    var json = JsonSerializer.Serialize(trajectory, TrajectoryJsonContext.Default.TrajectoryIR);
    using var document = JsonDocument.Parse(json);
    Equal("meta", document.RootElement.GetProperty("Records")[0].GetProperty("kind").GetString());
    Equal(
        "assistant_tool_calls",
        document.RootElement.GetProperty("Records")[1].GetProperty("kind").GetString());
}

static void True(bool value)
{
    if (!value)
    {
        throw new InvalidOperationException("Expected true.");
    }
}

static void False(bool value) => True(!value);

static void Equal<T>(T expected, T actual)
{
    if (expected is System.Collections.IEnumerable expectedItems &&
        actual is System.Collections.IEnumerable actualItems &&
        expected is not string)
    {
        if (!expectedItems.Cast<object?>().SequenceEqual(actualItems.Cast<object?>()))
        {
            throw new InvalidOperationException("Sequences differ.");
        }

        return;
    }

    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new InvalidOperationException($"Expected '{expected}', got '{actual}'.");
    }
}

static void NotEqual<T>(T left, T right)
{
    if (EqualityComparer<T>.Default.Equals(left, right))
    {
        throw new InvalidOperationException("Expected values to differ.");
    }
}
