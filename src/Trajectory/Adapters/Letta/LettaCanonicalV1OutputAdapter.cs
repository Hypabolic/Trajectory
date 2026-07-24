using System.Buffers;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using Hypabolic.Trajectory.Normalization;

namespace Hypabolic.Trajectory.Adapters.Letta;

public sealed class LettaCanonicalV1OutputAdapter : OutputSchemaAdapter<LettaCanonicalResult>
{
    public override string SchemaId => OutputSchemaIds.LettaCanonicalV1;
    public override string SchemaVersion => "1";

    public override LettaCanonicalResult Project(
        TrajectoryIR trajectory,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        var emitMeta = (trajectory.Config.SourceContext.BaseByteOffset ?? 0L) == 0L;
        var records = trajectory.Records
            .Where(record => emitMeta || record.Role != TrajectoryRole.Meta)
            .Select(record => MapRecord(trajectory, record))
            .ToArray();
        return new LettaCanonicalResult
        {
            Records = records,
            Diagnostics = trajectory.Diagnostics,
            NormalizerVersion = LettaCompatibilityVersion.Normalizer,
            CanonicalSchemaVersion = LettaCompatibilityVersion.CanonicalSchema,
            Config = new LettaCanonicalConfig
            {
                Bounds = trajectory.Config.Bounds,
                Filters = trajectory.Config.Filters,
            },
        };
    }

    public override string Serialize(
        LettaCanonicalResult output,
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
                if (diagnostic.InputLine is { } inputLine)
                    writer.WriteNumber("inputLine", inputLine);
                if (diagnostic.RecordIndex is { } recordIndex)
                    writer.WriteNumber("recordIndex", recordIndex);
                if (diagnostic.Count is { } count)
                    writer.WriteNumber("count", count);
                writer.WriteEndObject();
            }

            writer.WriteEndArray();
            writer.WriteString("normalizer_version", output.NormalizerVersion);
            writer.WriteNumber("canonical_schema_version", output.CanonicalSchemaVersion);
            writer.WriteStartObject("config");
            writer.WriteStartObject("bounds");
            writer.WriteStartObject("toolArguments");
            WriteNullableNumber(
                writer,
                "maxCharacters",
                output.Config.Bounds.ToolArguments.MaxCharacters);
            writer.WriteEndObject();
            writer.WriteStartObject("toolResults");
            WriteNullableNumber(
                writer,
                "maxCharacters",
                output.Config.Bounds.ToolResults.MaxCharacters);
            writer.WriteString(
                "strategy",
                output.Config.Bounds.ToolResults.Strategy ==
                    ToolResultTruncationStrategy.Head
                        ? "head"
                        : "head-tail");
            writer.WriteEndObject();
            writer.WriteEndObject();
            writer.WriteStartObject("filters");
            writer.WriteString(
                "toolResults",
                output.Config.Filters.ToolResults == ToolResultPolicy.Include
                    ? "include"
                    : "omit");
            writer.WriteEndObject();
            writer.WriteEndObject();
            writer.WriteEndObject();
        }

        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static LettaCanonicalRecord MapRecord(
        TrajectoryIR trajectory,
        IRRecord record)
    {
        var recordJson = CanonicalJson.Serialize(TrajectoryNormalizer.ToLettaJson(record));
        var content = record is MessageIR message ? message.Content : null;
        var calls = record as AssistantToolCallsIR;
        var result = record as ToolResultIR;
        return new LettaCanonicalRecord
        {
            SourceType = SourceName(trajectory.Source),
            SourceGroupId = trajectory.GroupId,
            StableSourceRecordId = record.Provenance.StableSourceRecordId,
            SourceIdentityKind = IdentityName(record.Provenance.SourceIdentityKind),
            SourceOrderId = record.Provenance.SourceOrderId,
            ComponentIndex = record.Provenance.ComponentIndex,
            RecordType = TrajectoryNormalizer.RecordType(record),
            RecordId = record.Id,
            RecordHash = record.Hashes.RecordSha256,
            ContentHash = record.Hashes.ContentSha256,
            SourceTimestamp = record.SourceTimestamp,
            RecordTimestamp = record.Timestamp,
            Content = content,
            ToolCallId = calls?.ToolCalls[0].Id ?? result?.ToolCallId,
            ToolName = calls?.ToolCalls[0].Name,
            ToolArgumentsJson = calls?.ToolCalls[0].ArgumentsJson,
            ToolResultJson = result?.Content,
            RecordJson = recordJson,
        };
    }

    private static void WriteRecord(Utf8JsonWriter writer, LettaCanonicalRecord record)
    {
        writer.WriteStartObject();
        writer.WriteString("source_type", record.SourceType);
        writer.WriteString("source_group_id", record.SourceGroupId);
        writer.WriteString("stable_source_record_id", record.StableSourceRecordId);
        writer.WriteString("source_identity_kind", record.SourceIdentityKind);
        writer.WriteString("source_order_id", record.SourceOrderId);
        writer.WriteNumber("component_index", record.ComponentIndex);
        writer.WriteString("record_type", record.RecordType);
        writer.WriteString("record_id", record.RecordId);
        writer.WriteString("record_hash", record.RecordHash);
        writer.WriteString("content_hash", record.ContentHash);
        WriteNullableTimestamp(writer, "source_timestamp", record.SourceTimestamp);
        WriteNullableTimestamp(writer, "record_timestamp", record.RecordTimestamp);
        WriteNullableString(writer, "content", record.Content);
        WriteNullableString(writer, "tool_call_id", record.ToolCallId);
        WriteNullableString(writer, "tool_name", record.ToolName);
        WriteNullableString(writer, "tool_arguments_json", record.ToolArgumentsJson);
        WriteNullableString(writer, "tool_result_json", record.ToolResultJson);
        writer.WriteString("record_json", record.RecordJson);
        writer.WriteEndObject();
    }

    private static void WriteNullableTimestamp(
        Utf8JsonWriter writer,
        string name,
        DateTimeOffset? value)
    {
        if (value is null)
            writer.WriteNull(name);
        else
            writer.WriteString(name, FormatTimestamp(value.Value));
    }

    private static void WriteNullableString(
        Utf8JsonWriter writer,
        string name,
        string? value)
    {
        if (value is null)
            writer.WriteNull(name);
        else
            writer.WriteString(name, value);
    }

    private static void WriteNullableNumber(
        Utf8JsonWriter writer,
        string name,
        int? value)
    {
        if (value is null)
            writer.WriteNull(name);
        else
            writer.WriteNumber(name, value.Value);
    }

    private static string FormatTimestamp(DateTimeOffset value) =>
        value.UtcDateTime.ToString(
            "yyyy-MM-dd'T'HH:mm:ss.fff'Z'",
            CultureInfo.InvariantCulture);

    private static string IdentityName(SourceIdentityKind kind) => kind switch
    {
        SourceIdentityKind.Native => "native",
        SourceIdentityKind.Location => "location",
        SourceIdentityKind.Content => "content",
        SourceIdentityKind.Synthetic => "synthetic",
        _ => throw new ArgumentOutOfRangeException(nameof(kind)),
    };

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
        _ => throw new ArgumentOutOfRangeException(nameof(source)),
    };
}
