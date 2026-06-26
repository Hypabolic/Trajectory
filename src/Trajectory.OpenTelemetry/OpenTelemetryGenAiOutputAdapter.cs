using System.Buffers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace Hypabolic.Trajectory.OpenTelemetry;

public sealed class OpenTelemetryGenAiOutputAdapter : OutputSchemaAdapter<OtelGenAiSpanSetV1>
{
    private readonly OtelGenAiProjectionOptions _projectionOptions;

    public OpenTelemetryGenAiOutputAdapter(OtelGenAiProjectionOptions? projectionOptions = null)
    {
        _projectionOptions = projectionOptions ?? new OtelGenAiProjectionOptions();
        _projectionOptions.Validate();
    }

    public override string SchemaId => OutputSchemaIds.OtelGenAiSpansV1;
    public override string SchemaVersion => "1";

    public override OtelGenAiSpanSetV1 Project(
        TrajectoryIR trajectory,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        var traceId = NonZero(Hash($"{trajectory.SourceName}|{trajectory.GroupId}")[..32]);
        var diagnostics = new List<OtelProjectionDiagnostic>();
        var spans = new List<OtelGenAiSpanV1>();
        var turns = BuildAgentTurns(trajectory, traceId, spans, diagnostics);
        AddModelSpans(trajectory, traceId, turns, spans, diagnostics);
        AddToolSpans(trajectory, traceId, turns, spans, diagnostics);
        AddWorkflowSpans(trajectory, traceId, turns, spans, diagnostics);

        return new OtelGenAiSpanSetV1
        {
            SchemaUrl = OtelGenAiConventions.SchemaUrl,
            TraceId = traceId,
            InstrumentationScope = OtelGenAiConventions.InstrumentationScope,
            InstrumentationVersion = TrajectoryVersion.Current,
            Spans = spans
                .OrderBy(static span => span.StartTime)
                .ThenBy(static span => span.Name, StringComparer.Ordinal)
                .ThenBy(static span => span.SpanId, StringComparer.Ordinal)
                .ToArray(),
            Diagnostics = diagnostics,
            ContentPolicy = new OtelContentPolicy
            {
                MessagesIncluded = _projectionOptions.IncludeMessages,
                ToolArgumentsIncluded = _projectionOptions.IncludeToolArguments,
                ToolResultsIncluded = _projectionOptions.IncludeToolResults,
                MaximumCharacters = _projectionOptions.MaximumContentCharacters,
            },
        };
    }

    public override string Serialize(
        OtelGenAiSpanSetV1 output,
        OutputProjectionOptions? options = null)
    {
        var buffer = new ArrayBufferWriter<byte>();
        WriteJson(buffer, output, options);
        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    public override void Write(
        Stream destination,
        OtelGenAiSpanSetV1 output,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(destination);
        using var writer = new Utf8JsonWriter(destination, WriterOptions(options));
        JsonSerializer.Serialize(writer, output, OtelGenAiJsonContext.Default.OtelGenAiSpanSetV1);
    }

    private IReadOnlyList<AgentTurn> BuildAgentTurns(
        TrajectoryIR trajectory,
        string traceId,
        List<OtelGenAiSpanV1> spans,
        List<OtelProjectionDiagnostic> diagnostics)
    {
        var records = trajectory.Records.Where(static record => record is not MetaIR).ToArray();
        var users = records
            .Select((record, index) => (record, index))
            .Where(static item => item.record.Role == TrajectoryRole.User)
            .ToArray();
        var turns = new List<AgentTurn>();
        for (var index = 0; index < users.Length; index++)
        {
            var first = users[index];
            var nextIndex = index + 1 < users.Length ? users[index + 1].index : records.Length;
            var segment = records[first.index..nextIndex];
            var last = segment.LastOrDefault(static record => record.SourceTimestamp is not null);
            if (first.record.SourceTimestamp is not { } start || last?.SourceTimestamp is not { } end)
            {
                diagnostics.Add(new OtelProjectionDiagnostic
                {
                    Code = "agent_timing_unavailable",
                    Message = "Agent span omitted because the logical turn lacks source-native boundaries.",
                    RecordId = first.record.Id,
                });
                continue;
            }

            var spanId = SpanId($"agent|{first.record.Id}");
            var attributes = new AttributeBuilder()
                .String("gen_ai.operation.name", "invoke_agent")
                .String("gen_ai.conversation.id", trajectory.GroupId)
                .String("hypabolic.trajectory.id", traceId)
                .String("hypabolic.trajectory.source", trajectory.SourceName)
                .String("hypabolic.trajectory.record.id", first.record.Id);
            if (_projectionOptions.IncludeMessages &&
                first.record is MessageIR userMessage)
            {
                attributes.String(
                    "gen_ai.input.messages",
                    Capture(userMessage.Content));
            }

            var assistant = segment.OfType<MessageIR>()
                .LastOrDefault(static record => record.Role == TrajectoryRole.Assistant);
            if (_projectionOptions.IncludeMessages && assistant is not null)
            {
                attributes.String("gen_ai.output.messages", Capture(assistant.Content));
            }

            spans.Add(new OtelGenAiSpanV1
            {
                TraceId = traceId,
                SpanId = spanId,
                Name = "invoke_agent",
                Kind = "INTERNAL",
                StartTime = start,
                EndTime = end < start ? start : end,
                Status = "UNSET",
                Attributes = attributes.Build(),
                Links = [],
            });
            turns.Add(new AgentTurn(first.index, nextIndex, start, end, spanId));
        }

        return turns;
    }

    private static void AddModelSpans(
        TrajectoryIR trajectory,
        string traceId,
        IReadOnlyList<AgentTurn> turns,
        List<OtelGenAiSpanV1> spans,
        List<OtelProjectionDiagnostic> diagnostics)
    {
        foreach (var invocation in trajectory.Execution.ModelInvocations)
        {
            if (invocation.StartedAt is not { } start ||
                invocation.CompletedAt is not { } end ||
                (invocation.Provider is null &&
                    invocation.RequestedModel is null &&
                    invocation.ResponseModel is null))
            {
                diagnostics.Add(new OtelProjectionDiagnostic
                {
                    Code = "model_span_omitted",
                    Message = "Model span omitted because source-native timing or provider/model metadata is incomplete.",
                    RecordId = invocation.Id,
                });
                continue;
            }

            var parent = turns.LastOrDefault(turn => start >= turn.Start && start <= turn.End);
            var attributes = new AttributeBuilder()
                .String("gen_ai.operation.name", "chat")
                .String("gen_ai.provider.name", invocation.Provider)
                .String("gen_ai.request.model", invocation.RequestedModel)
                .String("gen_ai.response.model", invocation.ResponseModel)
                .String("gen_ai.response.id", invocation.ResponseId)
                .Strings(
                    "gen_ai.response.finish_reasons",
                    invocation.StopReason is null ? null : [invocation.StopReason])
                .Integer("gen_ai.usage.input_tokens", invocation.Usage?.InputTokens)
                .Integer("gen_ai.usage.output_tokens", invocation.Usage?.OutputTokens)
                .Integer("gen_ai.usage.cache_read.input_tokens", invocation.Usage?.CacheReadTokens)
                .Integer("gen_ai.usage.cache_creation.input_tokens", invocation.Usage?.CacheWriteTokens)
                .String("hypabolic.trajectory.invocation.id", invocation.Id)
                .String("hypabolic.trajectory.api_family", invocation.ApiFamily);
            var model = invocation.RequestedModel ?? invocation.ResponseModel;
            spans.Add(new OtelGenAiSpanV1
            {
                TraceId = traceId,
                SpanId = SpanId($"model|{invocation.Id}"),
                ParentSpanId = parent?.SpanId,
                Name = model is null ? "chat" : $"chat {model}",
                Kind = "CLIENT",
                StartTime = start,
                EndTime = end < start ? start : end,
                Status = "UNSET",
                Attributes = attributes.Build(),
                Links = [],
            });
        }
    }

    private void AddToolSpans(
        TrajectoryIR trajectory,
        string traceId,
        IReadOnlyList<AgentTurn> turns,
        List<OtelGenAiSpanV1> spans,
        List<OtelProjectionDiagnostic> diagnostics)
    {
        var records = trajectory.Records.Where(static record => record is not MetaIR).ToArray();
        var results = records.OfType<ToolResultIR>()
            .GroupBy(static result => result.ToolCallId, StringComparer.Ordinal)
            .ToDictionary(static group => group.Key, static group => group.First(), StringComparer.Ordinal);
        for (var recordIndex = 0; recordIndex < records.Length; recordIndex++)
        {
            if (records[recordIndex] is not AssistantToolCallsIR calls)
            {
                continue;
            }

            foreach (var call in calls.ToolCalls)
            {
                if (!results.TryGetValue(call.Id, out var result) ||
                    calls.SourceTimestamp is not { } start ||
                    result.SourceTimestamp is not { } end)
                {
                    diagnostics.Add(new OtelProjectionDiagnostic
                    {
                        Code = "tool_span_omitted",
                        Message = "Tool span omitted because the call/result link or source-native timing is incomplete.",
                        RecordId = calls.Id,
                    });
                    continue;
                }

                var parent = turns.LastOrDefault(
                    turn => recordIndex >= turn.StartIndex && recordIndex < turn.EndIndex);
                var attributes = new AttributeBuilder()
                    .String("gen_ai.operation.name", "execute_tool")
                    .String("gen_ai.tool.name", call.Name)
                    .String("gen_ai.tool.call.id", call.Id)
                    .String("hypabolic.trajectory.call_record.id", calls.Id)
                    .String("hypabolic.trajectory.result_record.id", result.Id);
                if (_projectionOptions.IncludeToolArguments)
                {
                    attributes.String("gen_ai.tool.call.arguments", Capture(call.ArgumentsJson));
                }

                if (_projectionOptions.IncludeToolResults)
                {
                    attributes.String("gen_ai.tool.call.result", Capture(result.Content));
                }

                if (result.IsError)
                {
                    attributes.String("error.type", result.ToolName ?? call.Name);
                }

                spans.Add(new OtelGenAiSpanV1
                {
                    TraceId = traceId,
                    SpanId = SpanId($"tool|{call.Id}|{calls.Id}"),
                    ParentSpanId = parent?.SpanId,
                    Name = $"execute_tool {call.Name}",
                    Kind = "INTERNAL",
                    StartTime = start,
                    EndTime = end < start ? start : end,
                    Status = result.IsError ? "ERROR" : "UNSET",
                    Attributes = attributes.Build(),
                    Links = [],
                });
            }
        }
    }

    private static void AddWorkflowSpans(
        TrajectoryIR trajectory,
        string traceId,
        IReadOnlyList<AgentTurn> turns,
        List<OtelGenAiSpanV1> spans,
        List<OtelProjectionDiagnostic> diagnostics)
    {
        foreach (var workflow in trajectory.Execution.WorkflowInvocations)
        {
            if (workflow.StartedAt is not { } start || workflow.CompletedAt is not { } end)
            {
                diagnostics.Add(new OtelProjectionDiagnostic
                {
                    Code = "workflow_span_omitted",
                    Message = "Workflow span omitted because its explicit source-native timing is incomplete.",
                    RecordId = workflow.Id,
                });
                continue;
            }

            var parent = turns.LastOrDefault(turn => start >= turn.Start && start <= turn.End);
            var attributes = new AttributeBuilder()
                .String("gen_ai.operation.name", "invoke_workflow")
                .String("gen_ai.workflow.name", workflow.Name)
                .String("hypabolic.trajectory.workflow.id", workflow.Id)
                .String("hypabolic.trajectory.native_record.id", workflow.NativeRecordId);
            spans.Add(new OtelGenAiSpanV1
            {
                TraceId = traceId,
                SpanId = SpanId($"workflow|{workflow.Id}"),
                ParentSpanId = parent?.SpanId,
                Name = workflow.Name is null ? "invoke_workflow" : $"invoke_workflow {workflow.Name}",
                Kind = "INTERNAL",
                StartTime = start,
                EndTime = end < start ? start : end,
                Status = "UNSET",
                Attributes = attributes.Build(),
                Links = [],
            });
        }
    }

    private string Capture(string content)
    {
        var redacted = _projectionOptions.Redactor!.Redact(content);
        var runes = redacted.EnumerateRunes();
        var builder = new StringBuilder();
        var count = 0;
        foreach (var rune in runes)
        {
            if (count++ == _projectionOptions.MaximumContentCharacters)
            {
                break;
            }

            builder.Append(rune);
        }

        return builder.ToString();
    }

    private static string Hash(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    private static string SpanId(string value) => NonZero(Hash(value)[..16]);

    private static string NonZero(string value) =>
        value.All(static character => character == '0')
            ? value[..^1] + "1"
            : value;

    private static void WriteJson(
        IBufferWriter<byte> destination,
        OtelGenAiSpanSetV1 output,
        OutputProjectionOptions? options)
    {
        using var writer = new Utf8JsonWriter(destination, WriterOptions(options));
        JsonSerializer.Serialize(writer, output, OtelGenAiJsonContext.Default.OtelGenAiSpanSetV1);
    }

    private static JsonWriterOptions WriterOptions(OutputProjectionOptions? options) => new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        Indented = options?.WriteIndented ?? false,
    };

    private sealed record AgentTurn(
        int StartIndex,
        int EndIndex,
        DateTimeOffset Start,
        DateTimeOffset End,
        string SpanId);

    private sealed class AttributeBuilder
    {
        private readonly List<OtelAttributeV1> _attributes = [];

        public AttributeBuilder String(string key, string? value)
        {
            if (value is not null)
            {
                _attributes.Add(new OtelAttributeV1 { Key = key, StringValue = value });
            }

            return this;
        }

        public AttributeBuilder Integer(string key, long? value)
        {
            if (value is not null)
            {
                _attributes.Add(new OtelAttributeV1 { Key = key, IntegerValue = value });
            }

            return this;
        }

        public AttributeBuilder Strings(string key, IReadOnlyList<string>? value)
        {
            if (value is not null)
            {
                _attributes.Add(new OtelAttributeV1 { Key = key, StringValues = value });
            }

            return this;
        }

        public IReadOnlyList<OtelAttributeV1> Build() =>
            _attributes.OrderBy(static attribute => attribute.Key, StringComparer.Ordinal).ToArray();
    }
}
