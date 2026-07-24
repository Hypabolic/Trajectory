using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Trajectory.Adapters.Letta;

/// <summary>Writes one letta-trajectory-v1 trajectory record as JSONL.</summary>
public sealed class LettaTrajectoryV1OutputAdapter : IOutputSchemaAdapter
{
    public const string AdapterName = "letta-trajectory-v1";
    public const string CurrentSchemaVersion = "1";

    public string SchemaId => AdapterName;
    public string SchemaVersion => CurrentSchemaVersion;

    public string Project(TrajectoryIR trajectory, OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(trajectory);
        options ??= new OutputProjectionOptions();

        var messages = new List<LettaMessage>();
        foreach (var item in trajectory.Records)
        {
            switch (item)
            {
                case AssistantToolCallsIR assistant:
                    messages.Add(new LettaMessage(
                        assistant.Id,
                        assistant.Order,
                        TrajectoryRoles.Assistant,
                        assistant.Content,
                        options.IncludeTimestamps ? assistant.Timestamp : null,
                        assistant.ToolCalls.Select(static call => new LettaToolCall(
                            call.Id,
                            call.Name,
                            call.ArgumentsJson)).ToArray(),
                        null));
                    break;
                case MessageIR message:
                    messages.Add(new LettaMessage(
                        message.Id,
                        message.Order,
                        message.Role,
                        message.Content,
                        options.IncludeTimestamps ? message.Timestamp : null,
                        null,
                        null));
                    break;
                case ToolResultIR result:
                    if (options.OmitToolResults)
                    {
                        break;
                    }

                    messages.Add(new LettaMessage(
                        result.Id,
                        result.Order,
                        TrajectoryRoles.Tool,
                        result.Content,
                        options.IncludeTimestamps ? result.Timestamp : null,
                        null,
                        new LettaToolResult(
                            result.ToolCallId,
                            result.ToolName,
                            result.Content,
                            result.IsError)));
                    break;
            }
        }

        var record = new LettaTrajectoryRecord(
            AdapterName,
            trajectory.GroupId ?? DeterministicIdentity.Create(
                "trajectory",
                trajectory.Source,
                string.Join("\n", trajectory.Records.Select(static record => record.Id))),
            trajectory.Source,
            messages,
            options.IncludeDiagnostics ? trajectory.Diagnostics : null);

        var serializerOptions = new JsonSerializerOptions
        {
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            WriteIndented = options.WriteIndented,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
        };
        var context = new TrajectoryJsonContext(serializerOptions);
        var json = JsonSerializer.Serialize(record, context.LettaTrajectoryRecord);

        return options.AppendFinalNewline ? string.Concat(json, "\n") : json;
    }
}
