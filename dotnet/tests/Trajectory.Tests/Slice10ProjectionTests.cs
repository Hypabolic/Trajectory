using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Hypabolic.Trajectory.Adapters.OpenAi;
using Hypabolic.Trajectory.Adapters.Streaming;
using Hypabolic.Trajectory.OpenTelemetry;
using Hypabolic.Trajectory.Testing;
using OpenTelemetry;
using OpenTelemetry.Trace;
using Xunit;

namespace Hypabolic.Trajectory.Tests;

public sealed class Slice10ProjectionTests
{
    [Fact]
    public void OpenAiProjectionMapsToolsAndExplicitlyOmitsReasoning()
    {
        var trajectory = Normalize(TrajectorySource.Pi, "pi/tool-calls/input.jsonl");
        var adapter = new OpenAiChatMessagesOutputAdapter();

        var output = adapter.Project(trajectory);
        var json = adapter.Serialize(output);

        Assert.Equal(OpenAiReasoningPolicy.Omit, output.ReasoningPolicy);
        Assert.DoesNotContain(output.Messages, static message =>
            message.Content?.Contains("I need to create", StringComparison.Ordinal) == true);
        Assert.Contains(output.Messages, static message =>
            message.Role == "assistant" &&
            message.ToolCalls?.Any(call => call.Id == "toolu_pi_1" &&
                call.Function.Name == "write") == true);
        Assert.Contains(output.Messages, static message =>
            message.Role == "tool" && message.ToolCallId == "toolu_pi_2");
        Assert.Equal(json, adapter.Serialize(adapter.Project(trajectory)));
    }

    [Fact]
    public void MinimalJsonlWritesDirectlyAndDeterministically()
    {
        var trajectory = Normalize(TrajectorySource.Codex, "codex/full/input.jsonl");
        var engine = TrajectoryEngine.CreateDefault();
        using var stream = new MemoryStream();

        engine.ProjectToStream(
            trajectory,
            OutputSchemaIds.JsonlMinimal,
            stream,
            new OutputProjectionOptions { WriteIndented = true });
        var streamed = Encoding.UTF8.GetString(stream.ToArray());
        var materialized = engine.ProjectJson(trajectory, OutputSchemaIds.JsonlMinimal);

        Assert.Equal(materialized, streamed);
        Assert.EndsWith("\n", streamed, StringComparison.Ordinal);
        foreach (var line in streamed.Split('\n', StringSplitOptions.RemoveEmptyEntries))
        {
            using var document = JsonDocument.Parse(line);
            Assert.True(document.RootElement.TryGetProperty("id", out _));
            Assert.True(document.RootElement.TryGetProperty("order", out _));
        }
    }

    [Fact]
    public void CustomSourceAndOutputRegistrationArePublicAndTypeSafe()
    {
        var pi = Normalize(TrajectorySource.Pi, "pi/tool-calls/input.jsonl");
        var engine = new TrajectoryEngine()
            .AddSourceAdapter(new FixedSourceAdapter(pi))
            .AddOutputAdapter(new MinimalJsonlOutputAdapter());

        var normalized = engine.NormalizeToIR(new NormalizeInput
        {
            Source = TrajectorySource.LettaCode,
            Transcript = "custom",
        });

        Assert.Same(pi, normalized);
        var exception = Assert.Throws<InvalidOperationException>(() =>
            engine.Project<OpenAiChatProjection>(normalized, OutputSchemaIds.JsonlMinimal));
        Assert.Contains(typeof(MinimalJsonlProjection).FullName!, exception.Message);
    }

    [Fact]
    public void PiProjectionProducesDeterministicAgentAndToolSpansWithoutSensitiveContent()
    {
        var trajectory = Normalize(TrajectorySource.Pi, "pi/tool-calls/input.jsonl");
        var adapter = new OpenTelemetryGenAiOutputAdapter();

        var first = adapter.Project(trajectory);
        var second = adapter.Project(trajectory);

        Assert.Equal(OtelGenAiConventions.SchemaUrl, first.SchemaUrl);
        Assert.Equal(adapter.Serialize(first), adapter.Serialize(second));
        Assert.Single(first.Spans, static span =>
            Attribute(span, "gen_ai.operation.name") == "invoke_agent");
        Assert.Equal(2, first.Spans.Count(static span =>
            Attribute(span, "gen_ai.operation.name") == "execute_tool"));
        Assert.DoesNotContain(first.Spans.SelectMany(static span => span.Attributes), static attribute =>
            attribute.Key is "gen_ai.input.messages" or
                "gen_ai.output.messages" or
                "gen_ai.tool.call.arguments" or
                "gen_ai.tool.call.result");
        Assert.DoesNotContain(first.Spans, static span =>
            Attribute(span, "gen_ai.operation.name") == "chat");
        Assert.Equal(3, first.Diagnostics.Count(static item => item.Code == "model_span_omitted"));
    }

    [Fact]
    public void CodexIsASecondDeterministicAgentAndToolProjection()
    {
        var trajectory = Normalize(TrajectorySource.Codex, "codex/full/input.jsonl");
        var output = new OpenTelemetryGenAiOutputAdapter().Project(trajectory);

        Assert.Contains(output.Spans, static span =>
            Attribute(span, "gen_ai.operation.name") == "invoke_agent");
        Assert.Contains(output.Spans, static span =>
            Attribute(span, "gen_ai.operation.name") == "execute_tool");
        Assert.All(output.Spans.Where(static span => span.ParentSpanId is not null), span =>
            Assert.Contains(output.Spans, parent => parent.SpanId == span.ParentSpanId));
    }

    [Fact]
    public void ModelSpanUsesOnlyNativeExecutionMetadata()
    {
        var trajectory = Normalize(TrajectorySource.Pi, "pi/tool-calls/input.jsonl");
        var invocation = trajectory.Execution.ModelInvocations[0] with
        {
            StartedAt = DateTimeOffset.Parse("2026-07-24T06:21:03.550Z"),
            CompletedAt = DateTimeOffset.Parse("2026-07-24T06:21:03.575Z"),
        };
        trajectory = trajectory with
        {
            Execution = new TrajectoryExecutionIR
            {
                ModelInvocations = [invocation],
                WorkflowInvocations =
                [
                    new WorkflowInvocationIR
                    {
                        Id = "workflow-native-1",
                        Name = "review",
                        StartedAt = DateTimeOffset.Parse("2026-07-24T06:21:03.548Z"),
                        CompletedAt = DateTimeOffset.Parse("2026-07-24T06:21:03.690Z"),
                    },
                ],
            },
        };

        var output = new OpenTelemetryGenAiOutputAdapter().Project(trajectory);
        var model = Assert.Single(output.Spans, static span =>
            Attribute(span, "gen_ai.operation.name") == "chat");

        Assert.Equal("anthropic", Attribute(model, "gen_ai.provider.name"));
        Assert.Equal("claude-sonnet-5", Attribute(model, "gen_ai.request.model"));
        Assert.Equal(120, IntegerAttribute(model, "gen_ai.usage.input_tokens"));
        Assert.NotNull(model.ParentSpanId);
        var workflow = Assert.Single(output.Spans, static span =>
            Attribute(span, "gen_ai.operation.name") == "invoke_workflow");
        Assert.Equal("review", Attribute(workflow, "gen_ai.workflow.name"));
        Assert.NotNull(workflow.ParentSpanId);
    }

    [Fact]
    public void ContentCaptureRequiresRedactionAndAppliesBound()
    {
        Assert.Throws<InvalidOperationException>(() => new OpenTelemetryGenAiOutputAdapter(
            new OtelGenAiProjectionOptions { IncludeToolArguments = true }));
        var trajectory = Normalize(TrajectorySource.Pi, "pi/tool-calls/input.jsonl");
        var adapter = new OpenTelemetryGenAiOutputAdapter(new OtelGenAiProjectionOptions
        {
            IncludeToolArguments = true,
            IncludeToolResults = true,
            MaximumContentCharacters = 12,
            Redactor = new SafeRedactor(),
        });

        var output = adapter.Project(trajectory);
        var captured = output.Spans.SelectMany(static span => span.Attributes)
            .Where(static attribute => attribute.Key is
                "gen_ai.tool.call.arguments" or "gen_ai.tool.call.result")
            .ToArray();

        Assert.NotEmpty(captured);
        Assert.All(captured, static attribute =>
        {
            Assert.True(attribute.StringValue!.Length <= 12);
            Assert.DoesNotContain("notes.txt", attribute.StringValue, StringComparison.Ordinal);
        });
    }

    [Fact]
    public void ActivitySourceEmissionFeedsOfficialOpenTelemetryPipelineShape()
    {
        var trajectory = Normalize(TrajectorySource.Pi, "pi/tool-calls/input.jsonl");
        var output = new OpenTelemetryGenAiOutputAdapter().Project(trajectory);
        var stopped = new List<Activity>();
        using var listener = new ActivityListener
        {
            ShouldListenTo = source => source.Name ==
                TrajectoryOpenTelemetryExtensions.ActivitySourceName,
            Sample = static (ref ActivityCreationOptions<ActivityContext> _) =>
                ActivitySamplingResult.AllDataAndRecorded,
            ActivityStopped = stopped.Add,
        };
        ActivitySource.AddActivityListener(listener);

        using var emitter = new OpenTelemetryGenAiActivityEmitter();
        var emitted = emitter.Emit(output);

        Assert.Equal(3, emitted);
        Assert.Equal(3, stopped.Count);
        Assert.All(stopped, activity =>
            Assert.Equal(OtelGenAiConventions.SchemaUrl, activity.GetTagItem("otel.schema_url")));
    }

    [Fact]
    public void OfficialOpenTelemetrySdkAcceptsTrajectorySourceRegistration()
    {
        using var provider = Sdk.CreateTracerProviderBuilder()
            .AddTrajectoryGenAi()
            .Build();

        Assert.NotNull(provider);
    }

    [Fact]
    public void AdapterTestKitChecksProjectionAndStreamingIdentity()
    {
        var trajectory = Normalize(TrajectorySource.Pi, "pi/tool-calls/input.jsonl");

        var result = AdapterContractTestKit.VerifyDeterministicOutput(
            new MinimalJsonlOutputAdapter(),
            trajectory);

        Assert.Equal(OutputSchemaIds.JsonlMinimal, result.SchemaId);
        Assert.True(result.Utf8Bytes > 0);
    }

    private static TrajectoryIR Normalize(TrajectorySource source, string fixture)
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Fixtures", fixture);
        return TrajectoryEngine.CreateDefault().NormalizeToIR(new NormalizeInput
        {
            Source = source,
            Transcript = File.ReadAllText(path),
        });
    }

    private static string? Attribute(OtelGenAiSpanV1 span, string key) =>
        span.Attributes.SingleOrDefault(attribute => attribute.Key == key)?.StringValue;

    private static long? IntegerAttribute(OtelGenAiSpanV1 span, string key) =>
        span.Attributes.SingleOrDefault(attribute => attribute.Key == key)?.IntegerValue;

    private sealed class SafeRedactor : IOtelContentRedactor
    {
        public string Redact(string content) =>
            content.Replace("notes.txt", "[path]", StringComparison.Ordinal);
    }

    private sealed class FixedSourceAdapter(TrajectoryIR trajectory) : ITrajectorySourceAdapter
    {
        public TrajectorySource Source => TrajectorySource.LettaCode;
        public TrajectoryIR Normalize(NormalizeInput input) => trajectory;
    }
}
