using System.Buffers;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace Hypabolic.Trajectory.Adapters.Hypabolic;

public sealed class HypabolicTrajectoryV1OutputAdapter : OutputSchemaAdapter<HypabolicTrajectoryV1>
{
    public override string SchemaId => OutputSchemaIds.HypabolicTrajectoryV1;
    public override string SchemaVersion => "1";

    public override HypabolicTrajectoryV1 Project(
        TrajectoryIR trajectory,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        var sourceType = SourceName(trajectory.Source);
        var offset = trajectory.Config.SourceContext.BaseByteOffset ?? 0L;
        return new HypabolicTrajectoryV1
        {
            SchemaId = SchemaId,
            SchemaVersion = 1,
            TrajectoryId = DeterministicIdentity.HashJson(writer =>
            {
                writer.WriteStartArray();
                writer.WriteStringValue(sourceType);
                writer.WriteStringValue(trajectory.GroupId);
                writer.WriteEndArray();
            }),
            Source = new HypabolicSourceV1
            {
                Type = sourceType,
                Name = trajectory.SourceName,
                GroupId = trajectory.GroupId,
                ProducerVersion = trajectory.ProducerVersion,
            },
            Segment = new HypabolicSegmentV1
            {
                Partial = trajectory.Config.SourceContext.Partial || offset > 0L,
                BaseByteOffset = offset,
            },
            Normalizer = new HypabolicNormalizerV1
            {
                Name = "Hypabolic.Trajectory",
                Version = TrajectoryVersion.Current,
            },
            Config = new HypabolicConfigV1
            {
                Bounds = new HypabolicBoundsV1
                {
                    ToolArguments = trajectory.Config.Bounds.ToolArguments,
                    ToolResults = trajectory.Config.Bounds.ToolResults,
                },
                Filters = new HypabolicFiltersV1
                {
                    ToolResults = trajectory.Config.Filters.ToolResults == ToolResultPolicy.Include
                        ? "include"
                        : "omit",
                },
            },
            Records = trajectory.Records.Select(MapRecord).ToArray(),
            Diagnostics = trajectory.Diagnostics,
        };
    }

    public override string Serialize(
        HypabolicTrajectoryV1 output,
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
            writer.WriteStartObject();
            writer.WriteString("schema_id", output.SchemaId);
            writer.WriteNumber("schema_version", output.SchemaVersion);
            writer.WriteString("trajectory_id", output.TrajectoryId);

            writer.WriteStartObject("source");
            writer.WriteString("type", output.Source.Type);
            writer.WriteString("name", output.Source.Name);
            writer.WriteString("group_id", output.Source.GroupId);
            if (output.Source.ProducerVersion is not null)
                writer.WriteString("producer_version", output.Source.ProducerVersion);
            writer.WriteEndObject();

            writer.WriteStartObject("segment");
            writer.WriteBoolean("partial", output.Segment.Partial);
            writer.WriteNumber("base_byte_offset", output.Segment.BaseByteOffset);
            writer.WriteEndObject();

            writer.WriteStartObject("normalizer");
            writer.WriteString("name", output.Normalizer.Name);
            writer.WriteString("version", output.Normalizer.Version);
            writer.WriteEndObject();

            writer.WriteStartObject("config");
            writer.WriteStartObject("bounds");
            writer.WriteStartObject("tool_arguments");
            if (output.Config.Bounds.ToolArguments.MaxCharacters is { } argumentMaximum)
                writer.WriteNumber("max_characters", argumentMaximum);
            else
                writer.WriteNull("max_characters");
            writer.WriteEndObject();
            writer.WriteStartObject("tool_results");
            if (output.Config.Bounds.ToolResults.MaxCharacters is { } resultMaximum)
                writer.WriteNumber("max_characters", resultMaximum);
            else
                writer.WriteNull("max_characters");
            writer.WriteString("strategy", output.Config.Bounds.ToolResults.Strategy == ToolResultTruncationStrategy.Head
                ? "head"
                : "head-tail");
            writer.WriteEndObject();
            writer.WriteEndObject();
            writer.WriteStartObject("filters");
            writer.WriteString("tool_results", output.Config.Filters.ToolResults);
            writer.WriteEndObject();
            writer.WriteEndObject();

            writer.WriteStartArray("records");
            foreach (var record in output.Records)
            {
                WriteRecord(writer, record);
            }
            writer.WriteEndArray();

            writer.WriteStartArray("diagnostics");
            foreach (var diagnostic in output.Diagnostics)
            {
                writer.WriteStartObject();
                writer.WriteString("code", diagnostic.Code);
                writer.WriteString("message", diagnostic.Message);
                if (diagnostic.InputLine is { } inputLine) writer.WriteNumber("input_line", inputLine);
                if (diagnostic.RecordIndex is { } recordIndex) writer.WriteNumber("record_index", recordIndex);
                if (diagnostic.Count is { } count) writer.WriteNumber("count", count);
                writer.WriteEndObject();
            }
            writer.WriteEndArray();
            writer.WriteEndObject();
        }

        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static HypabolicRecordV1 MapRecord(IRRecord record)
    {
        var common = new HypabolicRecordV1
        {
            Id = record.Id,
            Kind = KindName(record.Kind),
            Role = RoleName(record.Role),
            Order = record.Order,
            SourceTimestamp = record.SourceTimestamp,
            Timestamp = record.Timestamp,
            Provenance = new HypabolicProvenanceV1
            {
                StableSourceRecordId = record.Provenance.StableSourceRecordId,
                SourceIdentityKind = IdentityName(record.Provenance.SourceIdentityKind),
                SourceOrderId = record.Provenance.SourceOrderId,
                ComponentKey = record.Provenance.ComponentKey,
                ComponentIndex = record.Provenance.ComponentIndex,
                ComponentTypeOrdinal = record.Provenance.ComponentTypeOrdinal,
                ProducerVersion = record.Provenance.ProducerVersion,
                NativeRecordId = record.Provenance.NativeRecordId,
                SourceSequence = record.Provenance.SourceSequence,
                SourceOffset = record.Provenance.SourceOffset,
                SourceAnchorKind = record.Provenance.SourceAnchorKind is { } anchor
                    ? AnchorName(anchor)
                    : null,
            },
            Hashes = new HypabolicHashesV1
            {
                ContentSha256 = record.Hashes.ContentSha256,
                RecordSha256 = record.Hashes.RecordSha256,
            },
        };

        return record switch
        {
            MetaIR meta => common with
            {
                SourceName = meta.SourceName,
                Cwd = meta.Cwd,
                GitBranch = meta.GitBranch,
                Model = meta.Model,
                ProducerVersion = meta.ProducerVersion,
            },
            MessageIR message => common with { Content = message.Content },
            AssistantToolCallsIR assistant => common with
            {
                ToolCalls = assistant.ToolCalls.Select(static call => new HypabolicToolCallV1
                {
                    Id = call.Id,
                    Name = call.Name,
                    ArgumentsJson = call.ArgumentsJson,
                }).ToArray(),
            },
            ToolResultIR tool => common with
            {
                ToolCallId = tool.ToolCallId,
                ToolName = tool.ToolName,
                Content = tool.Content,
                IsError = tool.IsError,
            },
            _ => throw new ArgumentOutOfRangeException(nameof(record)),
        };
    }

    private static void WriteRecord(Utf8JsonWriter writer, HypabolicRecordV1 record)
    {
        writer.WriteStartObject();
        writer.WriteString("id", record.Id);
        writer.WriteString("kind", record.Kind);
        writer.WriteString("role", record.Role);
        writer.WriteNumber("order", record.Order);
        WriteNullableTimestamp(writer, "source_timestamp", record.SourceTimestamp);
        WriteNullableTimestamp(writer, "timestamp", record.Timestamp);

        if (record.SourceName is not null) writer.WriteString("source_name", record.SourceName);
        if (record.Cwd is not null) writer.WriteString("cwd", record.Cwd);
        if (record.GitBranch is not null) writer.WriteString("git_branch", record.GitBranch);
        if (record.Model is not null) writer.WriteString("model", record.Model);
        if (record.ProducerVersion is not null) writer.WriteString("producer_version", record.ProducerVersion);

        if (record.Kind == "assistant_tool_calls")
        {
            writer.WriteNull("content");
            writer.WriteStartArray("tool_calls");
            foreach (var call in record.ToolCalls ?? [])
            {
                writer.WriteStartObject();
                writer.WriteString("id", call.Id);
                writer.WriteString("name", call.Name);
                writer.WriteString("arguments_json", call.ArgumentsJson);
                writer.WriteEndObject();
            }
            writer.WriteEndArray();
        }
        else if (record.Content is not null)
        {
            writer.WriteString("content", record.Content);
        }

        if (record.ToolCallId is not null) writer.WriteString("tool_call_id", record.ToolCallId);
        if (record.ToolName is not null) writer.WriteString("tool_name", record.ToolName);
        if (record.IsError is { } isError) writer.WriteBoolean("is_error", isError);

        writer.WriteStartObject("provenance");
        writer.WriteString("stable_source_record_id", record.Provenance.StableSourceRecordId);
        writer.WriteString("source_identity_kind", record.Provenance.SourceIdentityKind);
        writer.WriteString("source_order_id", record.Provenance.SourceOrderId);
        writer.WriteString("component_key", record.Provenance.ComponentKey);
        writer.WriteNumber("component_index", record.Provenance.ComponentIndex);
        writer.WriteNumber("component_type_ordinal", record.Provenance.ComponentTypeOrdinal);
        if (record.Provenance.ProducerVersion is not null)
            writer.WriteString("producer_version", record.Provenance.ProducerVersion);
        if (record.Provenance.NativeRecordId is not null)
            writer.WriteString("native_record_id", record.Provenance.NativeRecordId);
        if (record.Provenance.SourceSequence is { } sourceSequence)
            writer.WriteNumber("source_sequence", sourceSequence);
        if (record.Provenance.SourceOffset is { } sourceOffset)
            writer.WriteNumber("source_offset", sourceOffset);
        if (record.Provenance.SourceAnchorKind is not null)
            writer.WriteString("source_anchor_kind", record.Provenance.SourceAnchorKind);
        writer.WriteEndObject();

        writer.WriteStartObject("hashes");
        writer.WriteString("content_sha256", record.Hashes.ContentSha256);
        writer.WriteString("record_sha256", record.Hashes.RecordSha256);
        writer.WriteEndObject();
        writer.WriteEndObject();
    }

    private static void WriteNullableTimestamp(
        Utf8JsonWriter writer,
        string propertyName,
        DateTimeOffset? value)
    {
        if (value is null)
        {
            writer.WriteNull(propertyName);
        }
        else
        {
            writer.WriteString(propertyName, value.Value.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'"));
        }
    }

    private static string SourceName(TrajectorySource source) => source switch
    {
        TrajectorySource.Pi => "pi",
        TrajectorySource.ClaudeCode => "claude-code",
        TrajectorySource.Codex => "codex",
        TrajectorySource.LettaCode => "letta-code",
        TrajectorySource.OpenClaw => "openclaw",
        TrajectorySource.OpenHands => "openhands",
        TrajectorySource.Hermes => "hermes",
        TrajectorySource.DeepAgents => "deepagents",
        TrajectorySource.Ahp => "ahp",
        _ => throw new ArgumentOutOfRangeException(nameof(source)),
    };

    private static string KindName(IRRecordKind kind) => kind switch
    {
        IRRecordKind.Meta => "meta",
        IRRecordKind.Message => "message",
        IRRecordKind.AssistantToolCalls => "assistant_tool_calls",
        IRRecordKind.ToolResult => "tool_result",
        _ => throw new ArgumentOutOfRangeException(nameof(kind)),
    };

    private static string RoleName(TrajectoryRole role) => role switch
    {
        TrajectoryRole.Meta => "meta",
        TrajectoryRole.User => "user",
        TrajectoryRole.Reasoning => "reasoning",
        TrajectoryRole.Assistant => "assistant",
        TrajectoryRole.Tool => "tool",
        _ => throw new ArgumentOutOfRangeException(nameof(role)),
    };

    private static string IdentityName(SourceIdentityKind kind) => kind switch
    {
        SourceIdentityKind.Native => "native",
        SourceIdentityKind.Location => "location",
        SourceIdentityKind.Content => "content",
        SourceIdentityKind.Synthetic => "synthetic",
        _ => throw new ArgumentOutOfRangeException(nameof(kind)),
    };

    private static string AnchorName(SourceAnchorKind kind) => kind switch
    {
        SourceAnchorKind.Byte => "byte",
        SourceAnchorKind.Ordinal => "ordinal",
        SourceAnchorKind.Row => "row",
        SourceAnchorKind.Sequence => "sequence",
        _ => throw new ArgumentOutOfRangeException(nameof(kind)),
    };
}
