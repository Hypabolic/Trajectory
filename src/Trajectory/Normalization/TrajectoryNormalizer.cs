using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory.Normalization;

internal sealed class TrajectoryNormalizer
{
    private static readonly DateTimeOffset SyntheticBase =
        new(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);

    private static readonly string[] NoisePrefixes =
    [
        "<local-command-caveat>",
        "<command-name>",
        "<command-message>",
        "<local-command-stdout>",
        "<local-command-stderr>",
        "<task-notification",
    ];

    private static readonly RecordHashes EmptyHashes = new()
    {
        ContentSha256 = new string('0', 64),
        RecordSha256 = new string('0', 64),
    };

    public TrajectoryIR Normalize(
        DecodedSession decoded,
        AppliedNormalizationConfig config)
    {
        ArgumentNullException.ThrowIfNull(decoded);
        ArgumentNullException.ThrowIfNull(config);

        var context = decoded.Context;
        var groupId = ResolveGroupId(
            context.SourceGroupId,
            config.SourceContext.GroupId);
        var sourceGroupResolved =
            !string.IsNullOrEmpty(context.SourceGroupId) ||
            !string.IsNullOrEmpty(config.SourceContext.GroupId);
        var partial = config.SourceContext.Partial ||
            (config.SourceContext.BaseByteOffset ?? 0L) > 0L;
        var diagnostics = decoded.Diagnostics.ToList();
        var plan = PlanEvents(decoded.Events);
        var body = new List<IRRecord>(decoded.Events.Count);
        var timestampAnchors = new Dictionary<int, DateTimeOffset>();
        var modelCounts = new Dictionary<string, int>(StringComparer.Ordinal);

        for (var eventIndex = 0; eventIndex < decoded.Events.Count; eventIndex++)
        {
            var sourceEvent = decoded.Events[eventIndex];
            if (!string.IsNullOrEmpty(sourceEvent.Model))
            {
                modelCounts[sourceEvent.Model] =
                    modelCounts.GetValueOrDefault(sourceEvent.Model) + 1;
            }

            var record = NormalizeEvent(
                sourceEvent,
                eventIndex,
                body.Count + 1,
                groupId,
                config,
                partial,
                plan,
                diagnostics);
            if (record is null)
            {
                continue;
            }

            if (sourceEvent.Timestamp is { } timestamp)
            {
                timestampAnchors[body.Count] = timestamp;
            }

            body.Add(record);
        }

        if (!partial)
        {
            if (!body.Any(static record => record.Role == TrajectoryRole.User))
            {
                throw new TrajectoryNormalizationException(
                    NormalizationErrorCode.MissingUserRecords,
                    "Transcript did not contain any normalizable user records.");
            }

            if (!body.Any(static record => record.Role == TrajectoryRole.Assistant))
            {
                throw new TrajectoryNormalizationException(
                    NormalizationErrorCode.MissingAssistantRecords,
                    "Transcript did not contain any normalizable assistant records.");
            }
        }

        var timestamps = FillTimestamps(
            body.Count,
            timestampAnchors,
            context,
            diagnostics);
        for (var index = 0; index < body.Count; index++)
        {
            var timestamp = timestamps[index];
            body[index] = StampAndHash(body[index], timestamp);
        }

        var model = ResolveModel(modelCounts);
        var meta = BuildMeta(context, groupId, model);
        Validate([meta, .. body], partial);

        return new TrajectoryIR
        {
            Source = context.Source,
            SourceName = context.SourceName,
            GroupId = groupId,
            SourceGroupResolved = sourceGroupResolved,
            ProducerVersion = context.ProducerVersion,
            Records = [meta, .. body],
            Diagnostics = diagnostics,
            Execution = new TrajectoryExecutionIR
            {
                ModelInvocations = decoded.ModelInvocations
                    .Select(invocation => MapInvocation(
                        invocation,
                        groupId,
                        config.SourceContext.BaseByteOffset ?? 0L))
                    .ToArray(),
            },
            Config = config,
        };
    }

    private static IRRecord? NormalizeEvent(
        DecodedEvent sourceEvent,
        int eventIndex,
        int recordIndex,
        string groupId,
        AppliedNormalizationConfig config,
        bool partial,
        EventPlan plan,
        List<TrajectoryDiagnostic> diagnostics)
    {
        switch (sourceEvent.Kind)
        {
            case DecodedEventKind.Message:
            {
                var content = sourceEvent.Content ?? string.Empty;
                if (string.IsNullOrWhiteSpace(content))
                {
                    return null;
                }

                var role = sourceEvent.Role ?? TrajectoryRole.Assistant;
                if (role == TrajectoryRole.User &&
                    NoisePrefixes.Any(prefix =>
                        content.TrimStart().StartsWith(prefix, StringComparison.Ordinal)))
                {
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.NoiseRecordDropped,
                        Message = "Dropped a harness-noise user record.",
                        RecordIndex = recordIndex,
                        InputLine = sourceEvent.InputLine,
                    });
                    return null;
                }

                return CreateMessage(
                    sourceEvent,
                    role,
                    content,
                    eventIndex,
                    recordIndex - 1,
                    groupId,
                    config,
                    plan);
            }
            case DecodedEventKind.Reasoning:
            {
                var content = sourceEvent.Content ?? string.Empty;
                return string.IsNullOrWhiteSpace(content)
                    ? null
                    : CreateMessage(
                        sourceEvent,
                        TrajectoryRole.Reasoning,
                        content,
                        eventIndex,
                        recordIndex - 1,
                        groupId,
                        config,
                        plan);
            }
            case DecodedEventKind.ToolCall:
            {
                var entry = plan.Calls[eventIndex];
                if (entry.Synthesized)
                {
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.ToolCallIdSynthesized,
                        Message = $"Synthesized tool-call ID {Quote(entry.SourceId)}.",
                        RecordIndex = recordIndex,
                        InputLine = sourceEvent.InputLine,
                    });
                }

                if (entry.Renamed)
                {
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.DuplicateToolCallId,
                        Message = $"Renamed duplicate tool-call ID {Quote(entry.SourceId)} to {Quote(entry.FinalId)}.",
                        RecordIndex = recordIndex,
                        InputLine = sourceEvent.InputLine,
                    });
                }

                var name = string.IsNullOrEmpty(sourceEvent.ToolName)
                    ? "unknown_tool"
                    : sourceEvent.ToolName;
                if (string.IsNullOrEmpty(sourceEvent.ToolName))
                {
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.UnknownToolName,
                        Message = $"Substituted {Quote(name)} for a missing tool name.",
                        RecordIndex = recordIndex,
                        InputLine = sourceEvent.InputLine,
                    });
                }

                var arguments = NormalizationText.ShrinkArguments(
                    sourceEvent.ToolArgumentsJson,
                    config.Bounds.ToolArguments.MaxCharacters);
                if (arguments.Reshaped)
                {
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.ToolArgumentsReshaped,
                        Message = $"Reshaped arguments for tool call {Quote(entry.FinalId)} into a JSON object.",
                        RecordIndex = recordIndex,
                        InputLine = sourceEvent.InputLine,
                    });
                }

                if (arguments.Truncated)
                {
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.ToolArgumentsTruncated,
                        Message = $"Truncated arguments for tool call {Quote(entry.FinalId)} to at most {config.Bounds.ToolArguments.MaxCharacters} Unicode code points.",
                        RecordIndex = recordIndex,
                        InputLine = sourceEvent.InputLine,
                    });
                }

                var call = new ToolCallIR
                {
                    Id = entry.FinalId,
                    Name = name,
                    ArgumentsJson = arguments.Arguments,
                };
                var semanticHash = ContentHashForToolCall(call);
                var provenance = BuildProvenance(
                    sourceEvent,
                    eventIndex,
                    groupId,
                    config,
                    plan,
                    semanticHash,
                    $"tool-call:{entry.FinalId}");
                return new AssistantToolCallsIR
                {
                    Id = DeterministicIdentity.RecordId(
                        groupId,
                        provenance.StableSourceRecordId,
                        provenance.ComponentKey),
                    Kind = IRRecordKind.AssistantToolCalls,
                    Role = TrajectoryRole.Assistant,
                    Order = recordIndex - 1,
                    SourceTimestamp = sourceEvent.Timestamp,
                    Timestamp = null,
                    ToolCalls = [call],
                    Provenance = provenance,
                    Hashes = EmptyHashes,
                };
            }
            case DecodedEventKind.ToolResult:
            {
                var sourceId = sourceEvent.ToolCallId ?? string.Empty;
                plan.OpenCalls.TryGetValue(sourceId, out var entries);
                var openEntry = entries?.FirstOrDefault(static item => !item.Consumed);
                var crossChunk = openEntry is null &&
                    partial &&
                    sourceId.Length > 0 &&
                    (entries is null || entries.Count == 0);
                if (openEntry is null && !crossChunk)
                {
                    var duplicate = entries is { Count: > 0 };
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = duplicate
                            ? DiagnosticCodes.DuplicateToolResult
                            : DiagnosticCodes.OrphanToolResult,
                        Message = duplicate
                            ? $"Dropped a duplicate result for tool call {Quote(sourceId)}."
                            : $"Dropped a tool result without a preceding call for {Quote(sourceId)}.",
                        RecordIndex = recordIndex,
                        InputLine = sourceEvent.InputLine,
                    });
                    return null;
                }

                if (openEntry is not null)
                {
                    openEntry.Consumed = true;
                }

                if (config.Filters.ToolResults == ToolResultPolicy.Omit)
                {
                    return null;
                }

                var finalId = openEntry?.FinalId ?? sourceId;
                var original = sourceEvent.Content ?? string.Empty;
                var content = NormalizationText.TruncateResult(
                    original,
                    config.Bounds.ToolResults.MaxCharacters,
                    config.Bounds.ToolResults.Strategy);
                if (!StringComparer.Ordinal.Equals(content, original))
                {
                    var strategy = config.Bounds.ToolResults.Strategy ==
                        ToolResultTruncationStrategy.Head
                            ? "head"
                            : "head-tail";
                    diagnostics.Add(new TrajectoryDiagnostic
                    {
                        Code = DiagnosticCodes.ToolResultTruncated,
                        Message = $"Truncated the result for tool call {Quote(finalId)} to at most {config.Bounds.ToolResults.MaxCharacters} Unicode code points using the {Quote(strategy)} strategy.",
                        RecordIndex = recordIndex,
                        InputLine = sourceEvent.InputLine,
                    });
                }

                var semanticHash = ContentHashForToolResult(content);
                var provenance = BuildProvenance(
                    sourceEvent,
                    eventIndex,
                    groupId,
                    config,
                    plan,
                    semanticHash,
                    $"tool-result:{finalId}");
                return new ToolResultIR
                {
                    Id = DeterministicIdentity.RecordId(
                        groupId,
                        provenance.StableSourceRecordId,
                        provenance.ComponentKey),
                    Kind = IRRecordKind.ToolResult,
                    Role = TrajectoryRole.Tool,
                    Order = recordIndex - 1,
                    SourceTimestamp = sourceEvent.Timestamp,
                    Timestamp = null,
                    ToolCallId = finalId,
                    ToolName = sourceEvent.ToolName,
                    Content = content,
                    IsError = sourceEvent.IsError,
                    Provenance = provenance,
                    Hashes = EmptyHashes,
                };
            }
            default:
                throw new ArgumentOutOfRangeException(nameof(sourceEvent));
        }
    }

    private static MessageIR CreateMessage(
        DecodedEvent sourceEvent,
        TrajectoryRole role,
        string content,
        int eventIndex,
        int order,
        string groupId,
        AppliedNormalizationConfig config,
        EventPlan plan)
    {
        var type = role switch
        {
            TrajectoryRole.User => "user",
            TrajectoryRole.Reasoning => "reasoning",
            _ => "assistant",
        };
        var semanticHash = ContentHashForMessage(type, content);
        var componentKey = role == TrajectoryRole.Reasoning
            ? $"reasoning:{plan.ComponentTypeOrdinals[eventIndex]}"
            : $"message:{plan.ComponentTypeOrdinals[eventIndex]}";
        var provenance = BuildProvenance(
            sourceEvent,
            eventIndex,
            groupId,
            config,
            plan,
            semanticHash,
            componentKey);
        return new MessageIR
        {
            Id = DeterministicIdentity.RecordId(
                groupId,
                provenance.StableSourceRecordId,
                provenance.ComponentKey),
            Kind = IRRecordKind.Message,
            Role = role,
            Order = order,
            SourceTimestamp = sourceEvent.Timestamp,
            Timestamp = null,
            Content = content,
            Provenance = provenance,
            Hashes = EmptyHashes,
        };
    }

    private static SourceRecordProvenance BuildProvenance(
        DecodedEvent sourceEvent,
        int eventIndex,
        string groupId,
        AppliedNormalizationConfig config,
        EventPlan plan,
        string contentHash,
        string componentKey)
    {
        var baseByteOffset = config.SourceContext.BaseByteOffset ?? 0L;
        string stableId;
        SourceIdentityKind identityKind;
        long? absoluteOffset = sourceEvent.SourceOffset;
        if (!string.IsNullOrEmpty(sourceEvent.NativeRecordId))
        {
            stableId = sourceEvent.NativeRecordId;
            identityKind = SourceIdentityKind.Native;
        }
        else if (sourceEvent.SourceOffset is { } offset)
        {
            var anchor = sourceEvent.SourceAnchorKind ?? SourceAnchorKind.Ordinal;
            absoluteOffset = anchor == SourceAnchorKind.Byte
                ? checked(offset + baseByteOffset)
                : offset;
            stableId = DeterministicIdentity.LocationId(groupId, anchor, absoluteOffset.Value);
            identityKind = SourceIdentityKind.Location;
        }
        else if (sourceEvent.SourceSequence is { } sequence)
        {
            stableId = DeterministicIdentity.LocationId(
                groupId,
                SourceAnchorKind.Sequence,
                sequence);
            identityKind = SourceIdentityKind.Location;
        }
        else
        {
            stableId = DeterministicIdentity.Sha256Hex(
                $"{groupId}|content|{RecordType(sourceEvent)}|{contentHash}|{sourceEvent.ComponentIndex}");
            identityKind = SourceIdentityKind.Content;
        }

        return new SourceRecordProvenance
        {
            StableSourceRecordId = stableId,
            SourceIdentityKind = identityKind,
            SourceOrderId = BuildSourceOrderId(
                sourceEvent.Timestamp,
                sourceEvent.SourceSequence,
                stableId),
            ComponentKey = componentKey,
            ComponentIndex = sourceEvent.ComponentIndex,
            ComponentTypeOrdinal = plan.ComponentTypeOrdinals[eventIndex],
            ProducerVersion = sourceEvent.ProducerVersion,
            NativeRecordId = sourceEvent.NativeRecordId,
            SourceSequence = sourceEvent.SourceSequence,
            SourceOffset = absoluteOffset,
            SourceAnchorKind = sourceEvent.SourceAnchorKind,
        };
    }

    private static MetaIR BuildMeta(
        DecodedSessionContext context,
        string groupId,
        string? model)
    {
        var provenance = new SourceRecordProvenance
        {
            StableSourceRecordId = "meta",
            SourceIdentityKind = SourceIdentityKind.Synthetic,
            SourceOrderId = "0|0000-00-00T00:00:00.000Z|00000000000000000000|meta",
            ComponentKey = "meta",
            ComponentIndex = 0,
            ComponentTypeOrdinal = 0,
        };
        var meta = new MetaIR
        {
            Id = DeterministicIdentity.RecordId(groupId, "meta", "meta"),
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
            Provenance = provenance,
            Hashes = EmptyHashes,
        };
        return meta with { Hashes = HashRecord(meta) };
    }

    private static IRRecord StampAndHash(IRRecord record, DateTimeOffset timestamp)
    {
        IRRecord stamped = record switch
        {
            MessageIR message => message with { Timestamp = timestamp },
            AssistantToolCallsIR calls => calls with { Timestamp = timestamp },
            ToolResultIR result => result with { Timestamp = timestamp },
            _ => throw new ArgumentOutOfRangeException(nameof(record)),
        };
        return stamped switch
        {
            MessageIR message => message with { Hashes = HashRecord(message) },
            AssistantToolCallsIR calls => calls with { Hashes = HashRecord(calls) },
            ToolResultIR result => result with { Hashes = HashRecord(result) },
            _ => throw new ArgumentOutOfRangeException(nameof(record)),
        };
    }

    private static RecordHashes HashRecord(IRRecord record)
    {
        var type = RecordType(record);
        var semantic = SemanticContent(record);
        var contentEnvelope = new JsonObject
        {
            ["type"] = type,
            ["content"] = semantic,
        };
        var recordJson = CanonicalJson.Serialize(ToLettaJson(record));
        return new RecordHashes
        {
            ContentSha256 = DeterministicIdentity.Sha256Hex(
                CanonicalJson.Serialize(contentEnvelope)),
            RecordSha256 = DeterministicIdentity.Sha256Hex(recordJson),
        };
    }

    internal static JsonObject ToLettaJson(IRRecord record)
    {
        var output = new JsonObject { ["role"] = RoleName(record.Role) };
        switch (record)
        {
            case MetaIR meta:
                output["source"] = meta.SourceName;
                if (meta.Cwd is not null) output["cwd"] = meta.Cwd;
                if (meta.GitBranch is not null) output["git_branch"] = meta.GitBranch;
                if (meta.Model is not null) output["model"] = meta.Model;
                break;
            case MessageIR message:
                output["content"] = message.Content;
                output["timestamp"] = FormatTimestamp(message.Timestamp!.Value);
                break;
            case AssistantToolCallsIR calls:
                output["content"] = null;
                output["tool_calls"] = new JsonArray(calls.ToolCalls
                    .Select(call => (JsonNode)new JsonObject
                    {
                        ["id"] = call.Id,
                        ["name"] = call.Name,
                        ["args"] = call.ArgumentsJson,
                    })
                    .ToArray());
                output["timestamp"] = FormatTimestamp(calls.Timestamp!.Value);
                break;
            case ToolResultIR result:
                output["tool_call_id"] = result.ToolCallId;
                output["content"] = result.Content;
                output["timestamp"] = FormatTimestamp(result.Timestamp!.Value);
                break;
        }

        return output;
    }

    private static JsonNode SemanticContent(IRRecord record) => record switch
    {
        MetaIR meta => new JsonObject
        {
            ["source"] = meta.SourceName,
            ["cwd"] = meta.Cwd,
            ["git_branch"] = meta.GitBranch,
            ["model"] = meta.Model,
        }.WithoutNulls(),
        MessageIR message => new JsonObject { ["content"] = message.Content },
        AssistantToolCallsIR calls => new JsonObject
        {
            ["name"] = calls.ToolCalls[0].Name,
            ["args"] = calls.ToolCalls[0].ArgumentsJson,
        },
        ToolResultIR result => new JsonObject { ["content"] = result.Content },
        _ => new JsonObject(),
    };

    private static string ContentHashForMessage(string type, string content) =>
        DeterministicIdentity.Sha256Hex(CanonicalJson.Serialize(new JsonObject
        {
            ["type"] = type,
            ["content"] = new JsonObject { ["content"] = content },
        }));

    private static string ContentHashForToolCall(ToolCallIR call) =>
        DeterministicIdentity.Sha256Hex(CanonicalJson.Serialize(new JsonObject
        {
            ["type"] = "assistant-tool-call",
            ["content"] = new JsonObject
            {
                ["name"] = call.Name,
                ["args"] = call.ArgumentsJson,
            },
        }));

    private static string ContentHashForToolResult(string content) =>
        DeterministicIdentity.Sha256Hex(CanonicalJson.Serialize(new JsonObject
        {
            ["type"] = "tool",
            ["content"] = new JsonObject { ["content"] = content },
        }));

    private static DateTimeOffset[] FillTimestamps(
        int count,
        IReadOnlyDictionary<int, DateTimeOffset> anchors,
        DecodedSessionContext context,
        List<TrajectoryDiagnostic> diagnostics)
    {
        if (count == 0)
        {
            return [];
        }

        if (anchors.Count == 0)
        {
            var start = context.CreatedAt ?? SyntheticBase;
            diagnostics.Add(new TrajectoryDiagnostic
            {
                Code = DiagnosticCodes.TimestampsSynthesized,
                Message = $"Synthesized timestamps for {count} normalized records.",
                Count = count,
            });
            return Enumerable.Range(0, count)
                .Select(index => start.AddSeconds(index * 15d))
                .ToArray();
        }

        var output = new DateTimeOffset[count];
        var indexes = anchors.Keys.Order().ToArray();
        var first = indexes[0];
        var last = indexes[^1];
        for (var index = 0; index < first; index++)
        {
            output[index] = FromUnixMilliseconds(
                anchors[first].ToUnixTimeMilliseconds() - ((first - index) * 1_000d));
        }

        for (var cursor = 0; cursor + 1 < indexes.Length; cursor++)
        {
            var startIndex = indexes[cursor];
            var endIndex = indexes[cursor + 1];
            var startMs = anchors[startIndex].ToUnixTimeMilliseconds();
            var spanMs = anchors[endIndex].ToUnixTimeMilliseconds() - startMs;
            var gap = endIndex - startIndex;
            output[startIndex] = FromUnixMilliseconds(startMs);
            for (var index = startIndex + 1; index < endIndex; index++)
            {
                output[index] = FromUnixMilliseconds(
                    startMs + (spanMs * (index - startIndex) / (double)gap));
            }
        }

        output[last] = FromUnixMilliseconds(anchors[last].ToUnixTimeMilliseconds());
        for (var index = last + 1; index < count; index++)
        {
            output[index] = FromUnixMilliseconds(
                anchors[last].ToUnixTimeMilliseconds() + ((index - last) * 1_000d));
        }

        var interpolated = count - anchors.Count;
        if (interpolated > 0)
        {
            diagnostics.Add(new TrajectoryDiagnostic
            {
                Code = DiagnosticCodes.TimestampsInterpolated,
                Message = $"Interpolated timestamps for {interpolated} normalized records.",
                Count = interpolated,
            });
        }

        return output;
    }

    private static DateTimeOffset FromUnixMilliseconds(double milliseconds) =>
        DateTimeOffset.FromUnixTimeMilliseconds((long)milliseconds);

    private static EventPlan PlanEvents(IReadOnlyList<DecodedEvent> events)
    {
        var calls = new Dictionary<int, PlannedCall>();
        var openCalls = new Dictionary<string, List<OpenCall>>(StringComparer.Ordinal);
        var usedIds = new HashSet<string>(StringComparer.Ordinal);
        var occurrences = new int[events.Count];
        var buckets = new string[events.Count];
        var occurrence = -1;
        for (var index = 0; index < events.Count; index++)
        {
            var sourceEvent = events[index];
            if (sourceEvent.ComponentIndex == 0)
            {
                occurrence++;
            }

            occurrences[index] = occurrence;
            buckets[index] = SemanticBucket(sourceEvent);
            if (sourceEvent.Kind != DecodedEventKind.ToolCall)
            {
                continue;
            }

            var sourceId = string.IsNullOrEmpty(sourceEvent.ToolCallId)
                ? $"call_{index + 1}"
                : sourceEvent.ToolCallId;
            var finalId = sourceId;
            var renamed = false;
            if (!usedIds.Add(finalId))
            {
                var suffix = 2;
                while (!usedIds.Add($"{sourceId}__{suffix}"))
                {
                    suffix++;
                }

                finalId = $"{sourceId}__{suffix}";
                renamed = true;
            }

            if (!openCalls.TryGetValue(sourceId, out var entries))
            {
                entries = [];
                openCalls[sourceId] = entries;
            }

            entries.Add(new OpenCall(finalId));
            calls[index] = new PlannedCall(
                sourceId,
                finalId,
                string.IsNullOrEmpty(sourceEvent.ToolCallId),
                renamed);
        }

        var ordinals = new int[events.Count];
        var seen = new Dictionary<string, int>(StringComparer.Ordinal);
        for (var index = 0; index < events.Count; index++)
        {
            var key = $"{occurrences[index]}:{buckets[index]}";
            ordinals[index] = seen.GetValueOrDefault(key);
            seen[key] = ordinals[index] + 1;
        }

        return new EventPlan(calls, openCalls, ordinals);
    }

    private static void Validate(IReadOnlyList<IRRecord> records, bool partial)
    {
        if (records.Count == 0 || records[0] is not MetaIR)
        {
            Invalid("Transcript must be a non-empty array beginning with meta.");
        }

        var allCallIds = records.OfType<AssistantToolCallsIR>()
            .SelectMany(static record => record.ToolCalls)
            .Select(static call => call.Id)
            .ToHashSet(StringComparer.Ordinal);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var call in records.OfType<AssistantToolCallsIR>()
                     .SelectMany(static record => record.ToolCalls))
        {
            if (!seen.Add(call.Id))
            {
                Invalid($"Duplicate tool-call ID {call.Id}.");
            }

            try
            {
                if (JsonNode.Parse(call.ArgumentsJson) is not JsonObject)
                {
                    Invalid("Tool-call args must encode a JSON object.");
                }
            }
            catch (JsonException)
            {
                Invalid("Tool-call args must contain valid JSON.");
            }
        }

        if (!partial && records.OfType<ToolResultIR>()
                .Any(result => !allCallIds.Contains(result.ToolCallId)))
        {
            Invalid("Tool result must reference a tool call.");
        }
    }

    private static void Invalid(string message) =>
        throw new TrajectoryNormalizationException(
            NormalizationErrorCode.InvalidNormalizedTranscript,
            message);

    private static string ResolveGroupId(string? detected, string? provided)
    {
        if (!string.IsNullOrEmpty(detected) &&
            !string.IsNullOrEmpty(provided) &&
            !StringComparer.Ordinal.Equals(detected, provided))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.SourceGroupConflict,
                $"Detected source group {Quote(detected)} conflicts with the provided source context group {Quote(provided)}.");
        }

        return !string.IsNullOrEmpty(detected)
            ? detected
            : !string.IsNullOrEmpty(provided)
                ? provided
                : "default";
    }

    private static string? ResolveModel(IReadOnlyDictionary<string, int> counts) =>
        counts.OrderByDescending(static pair => pair.Value)
            .ThenBy(static pair => pair.Key, StringComparer.Ordinal)
            .Select(static pair => pair.Key)
            .FirstOrDefault();

    private static ModelInvocationIR MapInvocation(
        DecodedModelInvocation invocation,
        string groupId,
        long baseByteOffset)
    {
        var absoluteOffset = invocation.SourceOffset is { } offset
            ? checked(offset + baseByteOffset)
            : (long?)null;
        var identity = !string.IsNullOrEmpty(invocation.NativeRecordId)
            ? invocation.NativeRecordId
            : absoluteOffset is { } location
                ? DeterministicIdentity.LocationId(groupId, SourceAnchorKind.Byte, location)
                : invocation.ResponseId ?? "model-invocation";
        var usage = invocation.InputTokens is null &&
            invocation.OutputTokens is null &&
            invocation.CacheReadTokens is null &&
            invocation.CacheWriteTokens is null &&
            invocation.TotalTokens is null
                ? null
                : new ModelTokenUsageIR
                {
                    InputTokens = invocation.InputTokens,
                    OutputTokens = invocation.OutputTokens,
                    CacheReadTokens = invocation.CacheReadTokens,
                    CacheWriteTokens = invocation.CacheWriteTokens,
                    TotalTokens = invocation.TotalTokens,
                };
        return new ModelInvocationIR
        {
            Id = DeterministicIdentity.RecordId(groupId, identity, "model-invocation"),
            NativeRecordId = invocation.NativeRecordId,
            SourceSequence = invocation.SourceSequence,
            SourceOffset = absoluteOffset,
            Provider = invocation.Provider,
            ApiFamily = invocation.ApiFamily,
            RequestedModel = invocation.RequestedModel,
            ResponseModel = invocation.ResponseModel,
            ResponseId = invocation.ResponseId,
            StopReason = invocation.StopReason,
            ProducerVersion = invocation.ProducerVersion,
            Usage = usage,
            StartedAt = invocation.StartedAt,
            FirstResponseAt = invocation.FirstResponseAt,
            CompletedAt = invocation.CompletedAt,
        };
    }

    private static string BuildSourceOrderId(
        DateTimeOffset? timestamp,
        long? sequence,
        string stableId) =>
        $"1|{(timestamp is null ? "0000-00-00T00:00:00.001Z" : FormatTimestamp(timestamp.Value))}|{(sequence ?? 0L).ToString(CultureInfo.InvariantCulture).PadLeft(20, '0')}|{stableId}";

    private static string FormatTimestamp(DateTimeOffset value) =>
        value.UtcDateTime.ToString(
            "yyyy-MM-dd'T'HH:mm:ss.fff'Z'",
            CultureInfo.InvariantCulture);

    private static string Quote(string value) =>
        CanonicalJson.Serialize(JsonValue.Create(value));

    private static string SemanticBucket(DecodedEvent sourceEvent) => sourceEvent.Kind switch
    {
        DecodedEventKind.Message => "message",
        DecodedEventKind.Reasoning => "reasoning",
        DecodedEventKind.ToolCall => "tool_call",
        DecodedEventKind.ToolResult => "tool_result",
        _ => throw new ArgumentOutOfRangeException(nameof(sourceEvent)),
    };

    private static string RecordType(DecodedEvent sourceEvent) => sourceEvent.Kind switch
    {
        DecodedEventKind.Message when sourceEvent.Role == TrajectoryRole.User => "user",
        DecodedEventKind.Message => "assistant",
        DecodedEventKind.Reasoning => "reasoning",
        DecodedEventKind.ToolCall => "assistant-tool-call",
        DecodedEventKind.ToolResult => "tool",
        _ => throw new ArgumentOutOfRangeException(nameof(sourceEvent)),
    };

    internal static string RecordType(IRRecord record) => record switch
    {
        MetaIR => "meta",
        MessageIR { Role: TrajectoryRole.User } => "user",
        MessageIR { Role: TrajectoryRole.Reasoning } => "reasoning",
        MessageIR => "assistant",
        AssistantToolCallsIR => "assistant-tool-call",
        ToolResultIR => "tool",
        _ => throw new ArgumentOutOfRangeException(nameof(record)),
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

    private sealed record EventPlan(
        IReadOnlyDictionary<int, PlannedCall> Calls,
        IReadOnlyDictionary<string, List<OpenCall>> OpenCalls,
        int[] ComponentTypeOrdinals);

    private sealed record PlannedCall(
        string SourceId,
        string FinalId,
        bool Synthesized,
        bool Renamed);

    private sealed class OpenCall(string finalId)
    {
        public string FinalId { get; } = finalId;
        public bool Consumed { get; set; }
    }
}

internal static class JsonObjectExtensions
{
    public static JsonObject WithoutNulls(this JsonObject value)
    {
        foreach (var key in value
                     .Where(static property => property.Value is null)
                     .Select(static property => property.Key)
                     .ToArray())
        {
            value.Remove(key);
        }

        return value;
    }
}
