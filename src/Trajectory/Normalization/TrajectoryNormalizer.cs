using System.Text.Json;
using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory.Normalization;

internal sealed class TrajectoryNormalizer
{
    private const string MissingTimeSentinel = "0000-00-00T00:00:00.001Z";
    private static readonly DateTimeOffset SyntheticBase =
        new(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);

    public TrajectoryIR Normalize(
        DecodedSession decoded,
        AppliedNormalizationConfig config,
        ReadOnlySpan<byte> transcriptUtf8)
    {
        var diagnostics = decoded.Diagnostics.ToList();
        var context = decoded.Context;
        var callerGroup = config.SourceContext.GroupId;
        if (context.SourceGroupId is not null && callerGroup is not null &&
            !string.Equals(context.SourceGroupId, callerGroup, StringComparison.Ordinal))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "Detected source group conflicts with the supplied source context group.");
        }

        var groupId = context.SourceGroupId ?? callerGroup ??
            DeterministicIdentity.StableGroupId(context.Source, transcriptUtf8);
        var partial = config.SourceContext.Partial || (config.SourceContext.BaseByteOffset ?? 0L) > 0L;
        var knownCalls = decoded.Events
            .Where(static item => item.Kind == DecodedEventKind.ToolCall)
            .Select(static item => item.ToolCallId)
            .Where(static item => !string.IsNullOrEmpty(item))
            .ToHashSet(StringComparer.Ordinal);
        var model = ResolveModel(decoded.Events);
        var records = new List<IRRecord>(decoded.Events.Count + 1);
        var typeOrdinals = new Dictionary<string, int>(StringComparer.Ordinal);
        var synthesizedTimestampCount = 0;
        var nextOrder = 0;

        var metaProvenance = new SourceRecordProvenance
        {
            StableSourceRecordId = "meta",
            SourceIdentityKind = SourceIdentityKind.Synthetic,
            SourceOrderId = "0|0000-00-00T00:00:00.000Z|00000000000000000000|meta",
            ComponentKey = "meta",
            ComponentIndex = 0,
            ComponentTypeOrdinal = 0,
        };
        var metaId = DeterministicIdentity.RecordId(groupId, "meta", "meta");
        records.Add(new MetaIR
        {
            Id = metaId,
            Kind = IRRecordKind.Meta,
            Role = TrajectoryRole.Meta,
            Order = -1,
            SourceTimestamp = null,
            Timestamp = null,
            SourceName = context.SourceName,
            Cwd = context.Cwd,
            GitBranch = context.GitBranch,
            Model = model,
            ProducerVersion = context.ProducerVersion,
            Provenance = metaProvenance,
            Hashes = HashMeta(
                metaId,
                context.SourceName,
                context.Cwd,
                context.GitBranch,
                model,
                context.ProducerVersion,
                metaProvenance),
        });

        foreach (var sourceEvent in decoded.Events)
        {
            if (sourceEvent.Kind == DecodedEventKind.ToolResult &&
                config.Filters.ToolResults == ToolResultPolicy.Omit)
            {
                continue;
            }

            if (sourceEvent.Kind == DecodedEventKind.ToolResult &&
                string.IsNullOrEmpty(sourceEvent.ToolCallId))
            {
                diagnostics.Add(new TrajectoryDiagnostic
                {
                    Code = DiagnosticCodes.OrphanToolResult,
                    Message = "Dropped a tool result without a tool-call ID.",
                    InputLine = sourceEvent.InputLine,
                    RecordIndex = nextOrder + 1,
                });
                continue;
            }

            if (sourceEvent.Kind == DecodedEventKind.ToolResult &&
                !partial &&
                !knownCalls.Contains(sourceEvent.ToolCallId!))
            {
                diagnostics.Add(new TrajectoryDiagnostic
                {
                    Code = DiagnosticCodes.OrphanToolResult,
                    Message = "Dropped a tool result whose call was not present in the transcript.",
                    InputLine = sourceEvent.InputLine,
                    RecordIndex = nextOrder + 1,
                });
                continue;
            }

            var timestamp = sourceEvent.Timestamp;
            if (timestamp is null)
            {
                timestamp = (context.CreatedAt ?? SyntheticBase).AddMilliseconds(nextOrder);
                synthesizedTimestampCount++;
            }

            var identity = ResolveSourceIdentity(sourceEvent, groupId, config.SourceContext.BaseByteOffset ?? 0L);
            var bucket = SemanticBucket(sourceEvent);
            var sourceOccurrence = sourceEvent.NativeRecordId is not null
                ? $"native:{sourceEvent.NativeRecordId}"
                : $"offset:{sourceEvent.SourceOffset?.ToString() ?? "missing"}";
            var ordinalKey = $"{sourceOccurrence}|{bucket}";
            var typeOrdinal = typeOrdinals.TryGetValue(ordinalKey, out var current) ? current : 0;
            typeOrdinals[ordinalKey] = typeOrdinal + 1;
            var componentKey = ComponentKey(sourceEvent, typeOrdinal);
            var sourceOrderId = BuildSourceOrderId(
                sourceEvent.Timestamp,
                sourceEvent.SourceSequence,
                identity.StableSourceRecordId);
            var provenance = new SourceRecordProvenance
            {
                StableSourceRecordId = identity.StableSourceRecordId,
                SourceIdentityKind = identity.Kind,
                SourceOrderId = sourceOrderId,
                ComponentKey = componentKey,
                ComponentIndex = sourceEvent.ComponentIndex,
                ComponentTypeOrdinal = typeOrdinal,
                NativeRecordId = sourceEvent.NativeRecordId,
                SourceSequence = sourceEvent.SourceSequence,
                SourceOffset = sourceEvent.SourceOffset is { } offset
                    ? checked(offset + (sourceEvent.SourceAnchorKind == SourceAnchorKind.Byte
                        ? config.SourceContext.BaseByteOffset ?? 0L
                        : 0L))
                    : null,
                SourceAnchorKind = sourceEvent.SourceAnchorKind,
            };
            var id = DeterministicIdentity.RecordId(groupId, identity.StableSourceRecordId, componentKey);
            var order = nextOrder++;

            switch (sourceEvent.Kind)
            {
                case DecodedEventKind.Message:
                case DecodedEventKind.Reasoning:
                {
                    var role = sourceEvent.Kind == DecodedEventKind.Reasoning
                        ? TrajectoryRole.Reasoning
                        : sourceEvent.Role ?? TrajectoryRole.Assistant;
                    var content = sourceEvent.Content ?? string.Empty;
                    records.Add(new MessageIR
                    {
                        Id = id,
                        Kind = IRRecordKind.Message,
                        Role = role,
                        Order = order,
                        SourceTimestamp = sourceEvent.Timestamp,
                        Timestamp = timestamp,
                        Content = content,
                        Provenance = provenance,
                        Hashes = HashMessage(
                            id,
                            role,
                            order,
                            sourceEvent.Timestamp,
                            timestamp.Value,
                            content,
                            provenance),
                    });
                    break;
                }
                case DecodedEventKind.ToolCall:
                {
                    if (string.IsNullOrEmpty(sourceEvent.ToolCallId))
                    {
                        throw new TrajectoryNormalizationException(
                            NormalizationErrorCode.InvalidNormalizedTranscript,
                            "Pi tool calls must carry a native tool-call ID in Slice 1.");
                    }

                    var call = new ToolCallIR
                    {
                        Id = sourceEvent.ToolCallId,
                        Name = string.IsNullOrEmpty(sourceEvent.ToolName) ? "unknown_tool" : sourceEvent.ToolName,
                        ArgumentsJson = string.IsNullOrEmpty(sourceEvent.ToolArgumentsJson)
                            ? "{}"
                            : sourceEvent.ToolArgumentsJson,
                    };
                    records.Add(new AssistantToolCallsIR
                    {
                        Id = id,
                        Kind = IRRecordKind.AssistantToolCalls,
                        Role = TrajectoryRole.Assistant,
                        Order = order,
                        SourceTimestamp = sourceEvent.Timestamp,
                        Timestamp = timestamp,
                        ToolCalls = [call],
                        Provenance = provenance,
                        Hashes = HashToolCall(
                            id,
                            order,
                            sourceEvent.Timestamp,
                            timestamp.Value,
                            call,
                            provenance),
                    });
                    break;
                }
                case DecodedEventKind.ToolResult:
                {
                    var content = sourceEvent.Content ?? string.Empty;
                    records.Add(new ToolResultIR
                    {
                        Id = id,
                        Kind = IRRecordKind.ToolResult,
                        Role = TrajectoryRole.Tool,
                        Order = order,
                        SourceTimestamp = sourceEvent.Timestamp,
                        Timestamp = timestamp,
                        ToolCallId = sourceEvent.ToolCallId!,
                        ToolName = sourceEvent.ToolName,
                        Content = content,
                        IsError = sourceEvent.IsError,
                        Provenance = provenance,
                        Hashes = HashToolResult(
                            id,
                            order,
                            sourceEvent.Timestamp,
                            timestamp.Value,
                            sourceEvent.ToolCallId!,
                            sourceEvent.ToolName,
                            content,
                            sourceEvent.IsError,
                            provenance),
                    });
                    break;
                }
                default:
                    throw new ArgumentOutOfRangeException();
            }
        }

        if (synthesizedTimestampCount > 0)
        {
            diagnostics.Add(new TrajectoryDiagnostic
            {
                Code = DiagnosticCodes.TimestampsSynthesized,
                Message = $"Synthesized timestamps for {synthesizedTimestampCount} normalized records.",
                Count = synthesizedTimestampCount,
            });
        }

        if (!partial)
        {
            if (!records.Any(static item => item.Role == TrajectoryRole.User))
            {
                throw new TrajectoryNormalizationException(
                    NormalizationErrorCode.MissingUserRecords,
                    "Transcript did not contain any normalizable user records.");
            }

            if (!records.Any(static item => item.Role == TrajectoryRole.Assistant))
            {
                throw new TrajectoryNormalizationException(
                    NormalizationErrorCode.MissingAssistantRecords,
                    "Transcript did not contain any normalizable assistant records.");
            }
        }

        return new TrajectoryIR
        {
            Source = context.Source,
            SourceName = context.SourceName,
            GroupId = groupId,
            ProducerVersion = context.ProducerVersion,
            Records = records,
            Diagnostics = diagnostics,
            Config = config,
        };
    }

    private static string? ResolveModel(IReadOnlyList<DecodedEvent> events) =>
        events.Where(static item => !string.IsNullOrEmpty(item.Model))
            .GroupBy(static item => item.Model!, StringComparer.Ordinal)
            .OrderByDescending(static group => group.Count())
            .ThenBy(static group => group.Key, StringComparer.Ordinal)
            .Select(static group => group.Key)
            .FirstOrDefault();

    private static (string StableSourceRecordId, SourceIdentityKind Kind) ResolveSourceIdentity(
        DecodedEvent sourceEvent,
        string groupId,
        long baseByteOffset)
    {
        if (!string.IsNullOrEmpty(sourceEvent.NativeRecordId))
        {
            return (sourceEvent.NativeRecordId, SourceIdentityKind.Native);
        }

        if (sourceEvent.SourceOffset is { } sourceOffset)
        {
            var absolute = sourceEvent.SourceAnchorKind == SourceAnchorKind.Byte
                ? checked(sourceOffset + baseByteOffset)
                : sourceOffset;
            return (
                DeterministicIdentity.LocationId(
                    groupId,
                    sourceEvent.SourceAnchorKind ?? SourceAnchorKind.Ordinal,
                    absolute),
                SourceIdentityKind.Location);
        }

        if (sourceEvent.SourceSequence is { } sourceSequence)
        {
            return (
                DeterministicIdentity.LocationId(groupId, SourceAnchorKind.Sequence, sourceSequence),
                SourceIdentityKind.Location);
        }

        return (
            DeterministicIdentity.Sha256Hex(
                $"{groupId}|content|{SemanticBucket(sourceEvent)}|{sourceEvent.Content}|{sourceEvent.ComponentIndex}"),
            SourceIdentityKind.Content);
    }

    private static string SemanticBucket(DecodedEvent sourceEvent) => sourceEvent.Kind switch
    {
        DecodedEventKind.Message => "message",
        DecodedEventKind.Reasoning => "reasoning",
        DecodedEventKind.ToolCall => "tool_call",
        DecodedEventKind.ToolResult => "tool_result",
        _ => throw new ArgumentOutOfRangeException(nameof(sourceEvent)),
    };

    private static string ComponentKey(DecodedEvent sourceEvent, int typeOrdinal) => sourceEvent.Kind switch
    {
        DecodedEventKind.Message => $"message:{typeOrdinal}",
        DecodedEventKind.Reasoning => $"reasoning:{typeOrdinal}",
        DecodedEventKind.ToolCall => $"tool-call:{sourceEvent.ToolCallId}",
        DecodedEventKind.ToolResult => $"tool-result:{sourceEvent.ToolCallId}",
        _ => throw new ArgumentOutOfRangeException(nameof(sourceEvent)),
    };

    private static string BuildSourceOrderId(
        DateTimeOffset? sourceTimestamp,
        long? sourceSequence,
        string stableSourceRecordId)
    {
        var timestamp = sourceTimestamp?.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'") ??
            MissingTimeSentinel;
        var sequence = (sourceSequence ?? 0L).ToString().PadLeft(20, '0');
        return $"1|{timestamp}|{sequence}|{stableSourceRecordId}";
    }

    private static RecordHashes HashMeta(
        string id,
        string sourceName,
        string? cwd,
        string? gitBranch,
        string? model,
        string? producerVersion,
        SourceRecordProvenance provenance)
    {
        var content = DeterministicIdentity.HashJson(writer =>
        {
            writer.WriteStartObject();
            if (cwd is not null) writer.WriteString("cwd", cwd);
            if (gitBranch is not null) writer.WriteString("git_branch", gitBranch);
            if (model is not null) writer.WriteString("model", model);
            if (producerVersion is not null) writer.WriteString("producer_version", producerVersion);
            writer.WriteString("source_name", sourceName);
            writer.WriteEndObject();
        });
        return new RecordHashes
        {
            ContentSha256 = content,
            RecordSha256 = DeterministicIdentity.Sha256Hex(
                $"{id}|meta|-1|{sourceName}|{cwd}|{gitBranch}|{model}|{producerVersion}|{ProvenanceText(provenance)}"),
        };
    }

    private static RecordHashes HashMessage(
        string id,
        TrajectoryRole role,
        int order,
        DateTimeOffset? sourceTimestamp,
        DateTimeOffset timestamp,
        string content,
        SourceRecordProvenance provenance) => new()
    {
        ContentSha256 = DeterministicIdentity.HashJson(writer =>
        {
            writer.WriteStartObject();
            writer.WriteString("content", content);
            writer.WriteEndObject();
        }),
        RecordSha256 = DeterministicIdentity.Sha256Hex(
            $"{id}|message|{role}|{order}|{sourceTimestamp:O}|{timestamp:O}|{content}|{ProvenanceText(provenance)}"),
    };

    private static RecordHashes HashToolCall(
        string id,
        int order,
        DateTimeOffset? sourceTimestamp,
        DateTimeOffset timestamp,
        ToolCallIR call,
        SourceRecordProvenance provenance) => new()
    {
        ContentSha256 = DeterministicIdentity.HashJson(writer =>
        {
            writer.WriteStartObject();
            writer.WriteString("args", call.ArgumentsJson);
            writer.WriteString("name", call.Name);
            writer.WriteEndObject();
        }),
        RecordSha256 = DeterministicIdentity.Sha256Hex(
            $"{id}|assistant_tool_calls|{order}|{sourceTimestamp:O}|{timestamp:O}|{call.Id}|{call.Name}|{call.ArgumentsJson}|{ProvenanceText(provenance)}"),
    };

    private static RecordHashes HashToolResult(
        string id,
        int order,
        DateTimeOffset? sourceTimestamp,
        DateTimeOffset timestamp,
        string toolCallId,
        string? toolName,
        string content,
        bool isError,
        SourceRecordProvenance provenance) => new()
    {
        ContentSha256 = DeterministicIdentity.HashJson(writer =>
        {
            writer.WriteStartObject();
            writer.WriteString("content", content);
            writer.WriteEndObject();
        }),
        RecordSha256 = DeterministicIdentity.Sha256Hex(
            $"{id}|tool_result|{order}|{sourceTimestamp:O}|{timestamp:O}|{toolCallId}|{toolName}|{content}|{isError}|{ProvenanceText(provenance)}"),
    };

    private static string ProvenanceText(SourceRecordProvenance value) =>
        JsonSerializer.Serialize(value, TrajectoryJsonContext.Default.SourceRecordProvenance);
}
