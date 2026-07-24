using System.Buffers;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace Hypabolic.Trajectory.Adapters.Letta;

public sealed class LettaTrajectoryV1OutputAdapter : OutputSchemaAdapter<LettaNormalizeResult>
{
    public override string SchemaId => OutputSchemaIds.LettaTrajectoryV1;
    public override string SchemaVersion => "1";

    public override LettaNormalizeResult Project(
        TrajectoryIR trajectory,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        var records = new List<LettaRecord>(trajectory.Records.Count);
        foreach (var record in trajectory.Records)
        {
            switch (record)
            {
                case MetaIR meta:
                    records.Add(new LettaMetaRecord
                    {
                        Role = "meta",
                        Source = trajectory.SourceName,
                        Cwd = meta.Cwd,
                        GitBranch = meta.GitBranch,
                        Model = meta.Model,
                    });
                    break;
                case MessageIR message:
                    records.Add(new LettaMessageRecord
                    {
                        Role = RoleName(message.Role),
                        Content = message.Content,
                        Timestamp = RequireTimestamp(message),
                    });
                    break;
                case AssistantToolCallsIR assistant:
                    records.Add(new LettaAssistantToolCallRecord
                    {
                        Role = "assistant",
                        ToolCalls = assistant.ToolCalls.Select(static call => new LettaToolCall
                        {
                            Id = call.Id,
                            Name = call.Name,
                            Args = call.ArgumentsJson,
                        }).ToArray(),
                        Timestamp = RequireTimestamp(assistant),
                    });
                    break;
                case ToolResultIR tool:
                    records.Add(new LettaToolResultRecord
                    {
                        Role = "tool",
                        ToolCallId = tool.ToolCallId,
                        Content = tool.Content,
                        Timestamp = RequireTimestamp(tool),
                    });
                    break;
                default:
                    throw new TrajectoryNormalizationException(
                        NormalizationErrorCode.InvalidNormalizedTranscript,
                        $"Unsupported IR record type {record.GetType().Name}.");
            }
        }

        return new LettaNormalizeResult
        {
            Records = records,
            Diagnostics = trajectory.Diagnostics,
        };
    }

    public override string Serialize(
        LettaNormalizeResult output,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(output);
        var buffer = new ArrayBufferWriter<byte>();
        using (var writer = new Utf8JsonWriter(buffer, new JsonWriterOptions
        {
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            Indented = options?.WriteIndented ?? false,
        }))
        {
            writer.WriteStartArray();
            foreach (var record in output.Records)
            {
                WriteRecord(writer, record);
            }
            writer.WriteEndArray();
        }

        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static void WriteRecord(Utf8JsonWriter writer, LettaRecord record)
    {
        writer.WriteStartObject();
        writer.WriteString("role", record.Role);
        switch (record)
        {
            case LettaMetaRecord meta:
                writer.WriteString("source", meta.Source);
                if (meta.Cwd is not null) writer.WriteString("cwd", meta.Cwd);
                if (meta.GitBranch is not null) writer.WriteString("git_branch", meta.GitBranch);
                if (meta.Model is not null) writer.WriteString("model", meta.Model);
                break;
            case LettaMessageRecord message:
                writer.WriteString("content", message.Content);
                writer.WriteString("timestamp", FormatTimestamp(message.Timestamp));
                break;
            case LettaAssistantToolCallRecord assistant:
                writer.WriteNull("content");
                writer.WriteStartArray("tool_calls");
                foreach (var call in assistant.ToolCalls)
                {
                    writer.WriteStartObject();
                    writer.WriteString("id", call.Id);
                    writer.WriteString("name", call.Name);
                    writer.WriteString("args", call.Args);
                    writer.WriteEndObject();
                }
                writer.WriteEndArray();
                writer.WriteString("timestamp", FormatTimestamp(assistant.Timestamp));
                break;
            case LettaToolResultRecord tool:
                writer.WriteString("tool_call_id", tool.ToolCallId);
                writer.WriteString("content", tool.Content);
                writer.WriteString("timestamp", FormatTimestamp(tool.Timestamp));
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(record));
        }
        writer.WriteEndObject();
    }

    private static DateTimeOffset RequireTimestamp(IRRecord record) =>
        record.Timestamp ?? throw new TrajectoryNormalizationException(
            NormalizationErrorCode.InvalidNormalizedTranscript,
            "Non-meta Letta records require a timestamp.");

    private static string RoleName(TrajectoryRole role) => role switch
    {
        TrajectoryRole.User => "user",
        TrajectoryRole.Reasoning => "reasoning",
        TrajectoryRole.Assistant => "assistant",
        TrajectoryRole.Tool => "tool",
        TrajectoryRole.Meta => "meta",
        _ => throw new ArgumentOutOfRangeException(nameof(role)),
    };

    private static string FormatTimestamp(DateTimeOffset value) =>
        value.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'");
}
