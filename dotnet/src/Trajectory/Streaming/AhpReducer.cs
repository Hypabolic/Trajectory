using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Hypabolic.Trajectory.Streaming;

/// <summary>
/// AHP Shape B action-log reducer (minimal complete subset for LS-07).
/// Pure: reduce ActionEnvelope batches into ChatState-like JSON, then
/// decode via existing Shape A path. No network. Protocol pin: 0.7.x.
/// </summary>
internal static class AhpReducer
{
    // Content-safe fixed messages (no action bodies, channels, or payloads).
    public const string MsgUnknownAction = "Ignored an unknown AHP action type.";
    public const string MsgForeignChannel = "Ignored an AHP action for a non-target channel.";
    public const string MsgInvalidActions = "AHP action batch must be JSONL envelopes or a JSON array.";
    public const string MsgBatchReorder =
        "AHP action batch serverSeq order must be strictly increasing.";
    public const string MsgBatchMixedSeq =
        "AHP action batch must not mix sequenced and unsequenced envelopes.";

    private static readonly HashSet<string> KnownChatActions = new(StringComparer.Ordinal)
    {
        "chat/turnStarted",
        "chat/responsePart",
        "chat/delta",
        "chat/reasoning",
        "chat/toolCallStart",
        "chat/toolCallDelta",
        "chat/toolCallReady",
        "chat/toolCallConfirmed",
        "chat/toolCallComplete",
        "chat/toolCallResultConfirmed",
        "chat/toolCallContentChanged",
        "chat/toolCallAuthRequired",
        "chat/toolCallAuthResolved",
        "chat/usage",
        "chat/turnComplete",
        "chat/turnCancelled",
        "chat/error",
        "chat/truncated",
        "chat/activityChanged",
        "chat/workingDirectorySet",
        "chat/workingDirectoryRemoved",
        "chat/inputRequested",
        "chat/inputAnswerChanged",
        "chat/inputCompleted",
    };

    public static JsonObject EmptyChatState(string? resource = null) =>
        new()
        {
            ["resource"] = resource,
            ["title"] = null,
            ["status"] = 1,
            ["activity"] = "",
            ["modifiedAt"] = null,
            ["origin"] = new JsonObject { ["kind"] = "user" },
            ["workingDirectories"] = new JsonArray(),
            ["turns"] = new JsonArray(),
            ["activeTurn"] = null,
        };

    public static List<JsonObject> ParseActionBatch(ReadOnlySpan<byte> data)
    {
        string text;
        try
        {
            text = Encoding.UTF8.GetString(data);
        }
        catch (DecoderFallbackException)
        {
            throw new ArgumentException(MsgInvalidActions);
        }

        var stripped = text.Trim();
        if (stripped.Length == 0)
        {
            return [];
        }

        // Prefer JSONL when multiple non-empty lines are present (Shape B default).
        var nonEmptyLines = new List<string>();
        foreach (var line in text.Split('\n'))
        {
            var trimmed = line.TrimEnd('\r');
            if (trimmed.Trim().Length > 0)
            {
                nonEmptyLines.Add(trimmed);
            }
        }

        if (nonEmptyLines.Count > 1)
        {
            var envelopes = new List<JsonObject>(nonEmptyLines.Count);
            foreach (var line in nonEmptyLines)
            {
                JsonNode? node;
                try
                {
                    node = JsonNode.Parse(line);
                }
                catch (JsonException)
                {
                    throw new ArgumentException(MsgInvalidActions);
                }

                if (node is not JsonObject obj)
                {
                    throw new ArgumentException(MsgInvalidActions);
                }

                envelopes.Add(obj);
            }

            return envelopes;
        }

        // Single payload: JSON array, single envelope object, or one JSONL line.
        JsonNode? parsed;
        try
        {
            parsed = JsonNode.Parse(stripped);
        }
        catch (JsonException)
        {
            throw new ArgumentException(MsgInvalidActions);
        }

        if (parsed is JsonArray arr)
        {
            var outList = new List<JsonObject>();
            foreach (var item in arr)
            {
                if (item is JsonObject o)
                {
                    outList.Add(o);
                }
            }

            return outList;
        }

        if (parsed is JsonObject single)
        {
            return [single];
        }

        throw new ArgumentException(MsgInvalidActions);
    }

    private sealed record NormalizedEnvelope(
        string? Channel,
        long? ServerSeq,
        JsonObject Action);

    private static NormalizedEnvelope? NormalizeEnvelope(JsonObject raw)
    {
        if (raw["action"] is JsonObject action)
        {
            var channel = raw["channel"] is JsonValue ch && ch.GetValueKind() == JsonValueKind.String
                ? ch.GetValue<string>()
                : null;
            var seq = AsFiniteNumber(raw["serverSeq"]);
            return new NormalizedEnvelope(channel, seq, action);
        }

        if (raw["type"] is JsonValue typeVal && typeVal.GetValueKind() == JsonValueKind.String)
        {
            var channel = raw["channel"] is JsonValue ch && ch.GetValueKind() == JsonValueKind.String
                ? ch.GetValue<string>()
                : null;
            var seq = AsFiniteNumber(raw["serverSeq"]);
            var bare = new JsonObject();
            foreach (var prop in raw)
            {
                if (prop.Key is "channel" or "serverSeq" or "origin")
                {
                    continue;
                }

                bare[prop.Key] = prop.Value?.DeepClone();
            }

            return new NormalizedEnvelope(channel, seq, bare);
        }

        return null;
    }

    private static long? AsFiniteNumber(JsonNode? node)
    {
        if (node is not JsonValue v)
        {
            return null;
        }

        if (v.GetValueKind() == JsonValueKind.Number)
        {
            if (v.TryGetValue<long>(out var l))
            {
                return l;
            }

            if (v.TryGetValue<double>(out var d) && !double.IsNaN(d) && !double.IsInfinity(d))
            {
                return (long)d;
            }
        }

        return null;
    }

    public static long? DetectSequenceGap(
        IReadOnlyList<JsonObject> envelopes,
        long? lastServerSeq,
        string? targetChannel)
    {
        var expected = lastServerSeq is null ? 1L : lastServerSeq.Value + 1;
        var seqs = new List<long>();
        foreach (var raw in envelopes)
        {
            var env = NormalizeEnvelope(raw);
            if (env is null || env.ServerSeq is null)
            {
                continue;
            }

            var seq = env.ServerSeq.Value;
            var ch = env.Channel;
            if (ch is not null && targetChannel is not null && ch != targetChannel)
            {
                continue;
            }

            if (ch is not null && !ch.StartsWith("ahp-chat:", StringComparison.Ordinal))
            {
                continue;
            }

            if (lastServerSeq is not null && seq <= lastServerSeq.Value)
            {
                continue;
            }

            seqs.Add(seq);
        }

        if (seqs.Count == 0)
        {
            return null;
        }

        seqs.Sort();
        if (lastServerSeq is not null && seqs[0] > expected)
        {
            return seqs[0];
        }

        var prev = seqs[0];
        for (var i = 1; i < seqs.Count; i++)
        {
            var s = seqs[i];
            if (s > prev + 1)
            {
                return prev + 1;
            }

            prev = s;
        }

        return null;
    }

    /// <summary>
    /// Validate original batch order under reorder=reject.
    /// Returns a fixed content-safe error message when invalid, else null.
    /// Does not sort. Rejects non-monotonic/duplicate seqs and mixed sequencing.
    /// </summary>
    public static string? ValidateAhpBatchOrder(
        IReadOnlyList<JsonObject> envelopes,
        string? targetChannel)
    {
        var hasSeq = false;
        var hasUnseq = false;
        long? lastSeq = null;
        foreach (var raw in envelopes)
        {
            var env = NormalizeEnvelope(raw);
            if (env is null)
            {
                continue;
            }

            var ch = env.Channel;
            if (ch is not null && targetChannel is not null && ch != targetChannel)
            {
                continue;
            }

            if (ch is not null && !ch.StartsWith("ahp-chat:", StringComparison.Ordinal))
            {
                continue;
            }

            if (env.ServerSeq is null)
            {
                hasUnseq = true;
                if (hasSeq)
                {
                    return MsgBatchMixedSeq;
                }

                continue;
            }

            hasSeq = true;
            if (hasUnseq)
            {
                return MsgBatchMixedSeq;
            }

            var seq = env.ServerSeq.Value;
            if (lastSeq is not null && seq <= lastSeq.Value)
            {
                return MsgBatchReorder;
            }

            lastSeq = seq;
        }

        return null;
    }

    public sealed record ReduceResult(
        JsonObject Chat,
        long? LastServerSeq,
        IReadOnlyList<(string Code, string Message)> Diagnostics,
        IReadOnlyList<long> Applied);

    /// <summary>
    /// Reduce ordered envelopes into chat state. Consumes original batch order
    /// and does not sort. Callers must reject non-monotonic / mixed batches via
    /// <see cref="ValidateAhpBatchOrder"/> first.
    /// </summary>
    public static ReduceResult ReduceAhpActions(
        JsonObject? chat,
        IReadOnlyList<JsonObject> envelopes,
        string? targetChannel,
        long? lastServerSeq)
    {
        var state = chat is not null
            ? (JsonObject)chat.DeepClone()
            : EmptyChatState(targetChannel);
        var diagnostics = new List<(string Code, string Message)>();
        var applied = new List<long>();
        var last = lastServerSeq;
        var channel = targetChannel ??
            (state["resource"] is JsonValue rv && rv.GetValueKind() == JsonValueKind.String
                ? rv.GetValue<string>()
                : null);
        if (channel is not null &&
            (state["resource"] is null || state["resource"]!.GetValueKind() == JsonValueKind.Null))
        {
            state["resource"] = channel;
        }

        // Preserve original batch order (reorder=reject). Do not sort-then-apply.
        var normalized = new List<NormalizedEnvelope>();
        foreach (var raw in envelopes)
        {
            var env = NormalizeEnvelope(raw);
            if (env is null)
            {
                diagnostics.Add((DiagnosticCodes.AhpUnknownAction, MsgUnknownAction));
                continue;
            }

            normalized.Add(env);
        }

        foreach (var env in normalized)
        {
            var action = env.Action;
            if (action["type"] is not JsonValue typeNode ||
                typeNode.GetValueKind() != JsonValueKind.String)
            {
                diagnostics.Add((DiagnosticCodes.AhpUnknownAction, MsgUnknownAction));
                continue;
            }

            var actionType = typeNode.GetValue<string>();
            var envChannel = env.Channel;

            // Lock channel from first chat-scoped action when unset.
            if (channel is null &&
                envChannel is not null &&
                envChannel.StartsWith("ahp-chat:", StringComparison.Ordinal))
            {
                channel = envChannel;
                state["resource"] = channel;
            }

            // Foreign channel: ignore (non-chat or different chat URI).
            if (envChannel is not null && channel is not null && envChannel != channel)
            {
                diagnostics.Add((DiagnosticCodes.AhpForeignChannel, MsgForeignChannel));
                continue;
            }

            if (envChannel is not null &&
                !envChannel.StartsWith("ahp-chat:", StringComparison.Ordinal))
            {
                diagnostics.Add((DiagnosticCodes.AhpForeignChannel, MsgForeignChannel));
                continue;
            }

            if (env.ServerSeq is null)
            {
                // Bare actions without seq: still reduce but do not advance serverSeq.
                if (!KnownChatActions.Contains(actionType))
                {
                    diagnostics.Add((DiagnosticCodes.AhpUnknownAction, MsgUnknownAction));
                    continue;
                }

                state = ApplyChatAction(state, action);
                continue;
            }

            var seq = env.ServerSeq.Value;
            // Already applied (idempotent replay of prefix)
            if (last is not null && seq <= last.Value)
            {
                continue;
            }

            if (!KnownChatActions.Contains(actionType))
            {
                diagnostics.Add((DiagnosticCodes.AhpUnknownAction, MsgUnknownAction));
                // Still advance seq so gaps are about missing numbers, not unknowns.
                last = seq;
                applied.Add(seq);
                continue;
            }

            state = ApplyChatAction(state, action);
            last = seq;
            applied.Add(seq);
        }

        if (channel is not null)
        {
            state["resource"] = channel;
        }

        return new ReduceResult(state, last, diagnostics, applied);
    }

    public static byte[] ShapeABytes(
        JsonObject chat,
        string protocolVersion = "0.7.0",
        JsonObject? session = null)
    {
        var envelope = new JsonObject
        {
            ["ahpProtocolVersion"] = protocolVersion,
            ["chat"] = chat.DeepClone(),
        };
        if (session is not null)
        {
            envelope["session"] = session.DeepClone();
        }

        return Encoding.UTF8.GetBytes(envelope.ToJsonString());
    }

    // ---------------------------------------------------------------------------
    // Chat action application (minimal complete reducer)
    // ---------------------------------------------------------------------------

    private static JsonObject ApplyChatAction(JsonObject state, JsonObject action)
    {
        var t = action["type"]?.GetValue<string>();
        return t switch
        {
            "chat/turnStarted" => TurnStarted(state, action),
            "chat/responsePart" => ResponsePart(state, action),
            "chat/delta" => Delta(state, action, "markdown"),
            "chat/reasoning" => Delta(state, action, "reasoning"),
            "chat/toolCallStart" => ToolCallStart(state, action),
            "chat/toolCallDelta" => UpdateTool(state, action, tc =>
            {
                if (tc["status"]?.GetValue<string>() != "streaming")
                {
                    return tc;
                }

                if (action["content"] is JsonValue content &&
                    content.GetValueKind() == JsonValueKind.String)
                {
                    var prev = tc["partialInput"] is JsonValue p &&
                               p.GetValueKind() == JsonValueKind.String
                        ? p.GetValue<string>()
                        : "";
                    tc["partialInput"] = prev + content.GetValue<string>();
                }

                if (action.ContainsKey("invocationMessage"))
                {
                    tc["invocationMessage"] = action["invocationMessage"]?.DeepClone();
                }

                return tc;
            }),
            "chat/toolCallReady" => UpdateTool(state, action, tc =>
            {
                var status = tc["status"]?.GetValue<string>();
                if (status is not ("streaming" or "running" or "pending-confirmation"))
                {
                    return tc;
                }

                if (action.ContainsKey("intention"))
                {
                    tc["intention"] = action["intention"]?.DeepClone();
                }

                if (action.ContainsKey("invocationMessage"))
                {
                    tc["invocationMessage"] = action["invocationMessage"]?.DeepClone();
                }

                if (action.ContainsKey("toolInput"))
                {
                    tc["toolInput"] = action["toolInput"]?.DeepClone();
                }

                if (action.ContainsKey("contributor"))
                {
                    tc["contributor"] = action["contributor"]?.DeepClone();
                }

                if (IsTruthy(action["confirmed"]))
                {
                    tc["status"] = "running";
                    tc["confirmed"] = action["confirmed"]?.DeepClone();
                }
                else
                {
                    tc["status"] = "pending-confirmation";
                }

                return tc;
            }),
            "chat/toolCallConfirmed" => UpdateTool(state, action, tc =>
            {
                if (tc["status"]?.GetValue<string>() != "pending-confirmation")
                {
                    return tc;
                }

                if (IsTruthy(action["approved"]))
                {
                    tc["status"] = "running";
                    tc["confirmed"] = action["confirmed"]?.DeepClone() ??
                                      JsonValue.Create("user-action");
                    if (action["editedToolInput"] is JsonValue edited &&
                        edited.GetValueKind() == JsonValueKind.String &&
                        tc["toolInput"] is JsonValue { } ti &&
                        ti.GetValueKind() == JsonValueKind.String)
                    {
                        tc["toolInput"] = edited.GetValue<string>();
                    }
                }
                else
                {
                    tc["status"] = "cancelled";
                    tc["success"] = false;
                    tc["reason"] = action["reason"]?.DeepClone() ?? JsonValue.Create("denied");
                    if (action.ContainsKey("reasonMessage"))
                    {
                        tc["reasonMessage"] = action["reasonMessage"]?.DeepClone();
                    }
                }

                return tc;
            }),
            "chat/toolCallComplete" => UpdateTool(state, action, tc =>
            {
                var st = tc["status"]?.GetValue<string>();
                if (st is not ("running" or "pending-confirmation" or "auth-required"))
                {
                    return tc;
                }

                var result = action["result"] as JsonObject ?? new JsonObject();
                if (st == "auth-required" && result["success"] is JsonValue s &&
                    s.GetValueKind() == JsonValueKind.True)
                {
                    return tc;
                }

                var requiresConfirm = IsTruthy(action["requiresResultConfirmation"]) &&
                                      st != "auth-required";
                foreach (var key in new[]
                         {
                             "success", "pastTenseMessage", "content", "structuredContent",
                             "error", "reasonMessage",
                         })
                {
                    if (result.ContainsKey(key))
                    {
                        tc[key] = result[key]?.DeepClone();
                    }
                }

                tc["status"] = requiresConfirm ? "pending-result-confirmation" : "completed";
                if ((tc["confirmed"] is null ||
                     tc["confirmed"]!.GetValueKind() == JsonValueKind.Null) &&
                    st == "pending-confirmation")
                {
                    tc["confirmed"] = "not-needed";
                }

                return tc;
            }),
            "chat/toolCallResultConfirmed" => UpdateTool(state, action, tc =>
            {
                if (tc["status"]?.GetValue<string>() != "pending-result-confirmation")
                {
                    return tc;
                }

                if (IsTruthy(action["approved"]))
                {
                    tc["status"] = "completed";
                }
                else
                {
                    tc["status"] = "cancelled";
                    tc["success"] = false;
                    tc["reason"] = "result-denied";
                }

                return tc;
            }),
            "chat/toolCallContentChanged" => UpdateTool(state, action, tc =>
            {
                if (tc["status"]?.GetValue<string>() != "running")
                {
                    return tc;
                }

                if (action.ContainsKey("content"))
                {
                    tc["content"] = action["content"]?.DeepClone();
                }

                return tc;
            }),
            "chat/toolCallAuthRequired" => UpdateTool(state, action, tc =>
            {
                if (tc["status"]?.GetValue<string>() != "running")
                {
                    return tc;
                }

                if (tc["contributor"] is not JsonObject contributor ||
                    contributor["kind"]?.GetValue<string>() != "mcp")
                {
                    return tc;
                }

                tc["status"] = "auth-required";
                if (action.ContainsKey("auth"))
                {
                    tc["auth"] = action["auth"]?.DeepClone();
                }

                return tc;
            }),
            "chat/toolCallAuthResolved" => UpdateTool(state, action, tc =>
            {
                if (tc["status"]?.GetValue<string>() != "auth-required")
                {
                    return tc;
                }

                tc["status"] = "running";
                tc.Remove("auth");
                return tc;
            }),
            "chat/usage" => Usage(state, action),
            "chat/turnComplete" => EndTurn(state, action, "complete"),
            "chat/turnCancelled" => EndTurn(state, action, "cancelled"),
            "chat/error" => EndTurn(state, action, "error"),
            "chat/truncated" => Truncated(state, action),
            "chat/activityChanged" => ActivityChanged(state, action),
            "chat/workingDirectorySet" => WorkingDirSet(state, action),
            "chat/workingDirectoryRemoved" => WorkingDirRemoved(state, action),
            "chat/inputRequested" or "chat/inputAnswerChanged" or "chat/inputCompleted" => state,
            _ => state,
        };
    }

    private static bool IsTruthy(JsonNode? node)
    {
        if (node is null)
        {
            return false;
        }

        if (node is JsonValue v)
        {
            return v.GetValueKind() switch
            {
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.Null => false,
                JsonValueKind.Number => v.TryGetValue<long>(out var l)
                    ? l != 0
                    : v.TryGetValue<double>(out var d) && d != 0,
                JsonValueKind.String => !string.IsNullOrEmpty(v.GetValue<string>()),
                _ => true,
            };
        }

        return true;
    }

    private static JsonObject? ActiveTurn(JsonObject state) =>
        state["activeTurn"] as JsonObject;

    private static JsonObject Clone(JsonObject state) => (JsonObject)state.DeepClone();

    private static JsonObject TurnStarted(JsonObject state, JsonObject action)
    {
        if (action["turnId"] is not JsonValue turnId ||
            turnId.GetValueKind() != JsonValueKind.String)
        {
            return state;
        }

        var next = Clone(state);
        JsonNode message = action["message"] is JsonObject msg
            ? msg.DeepClone()
            : new JsonObject
            {
                ["text"] = "",
                ["origin"] = new JsonObject { ["kind"] = "user" },
            };
        next["activeTurn"] = new JsonObject
        {
            ["id"] = turnId.GetValue<string>(),
            ["startedAt"] = action["startedAt"] is JsonValue sa &&
                            sa.GetValueKind() == JsonValueKind.String
                ? sa.GetValue<string>()
                : null,
            ["duration"] = null,
            ["message"] = message,
            ["responseParts"] = new JsonArray(),
            ["usage"] = null,
            ["state"] = "in-progress",
            ["error"] = null,
        };
        var activity = next["activity"] is JsonValue av &&
                       av.GetValueKind() == JsonValueKind.String
            ? av.GetValue<string>()
            : "";
        next["activity"] = string.IsNullOrEmpty(activity) ? "generating" : activity;
        return next;
    }

    private static JsonObject ResponsePart(JsonObject state, JsonObject action)
    {
        var next = Clone(state);
        var active = ActiveTurn(next);
        if (active is null ||
            active["id"]?.GetValue<string>() != action["turnId"]?.GetValue<string>() ||
            action["part"] is not JsonObject part)
        {
            return state;
        }

        var parts = active["responseParts"] as JsonArray ?? new JsonArray();
        var newParts = new JsonArray();
        foreach (var p in parts)
        {
            AppendJson(newParts, p?.DeepClone());
        }

        AppendJson(newParts, part.DeepClone());
        active["responseParts"] = newParts;
        next["activeTurn"] = active;
        return next;
    }

    private static JsonObject Delta(JsonObject state, JsonObject action, string partKind)
    {
        var next = Clone(state);
        var active = ActiveTurn(next);
        if (active is null ||
            active["id"]?.GetValue<string>() != action["turnId"]?.GetValue<string>() ||
            action["partId"] is not JsonValue partId ||
            partId.GetValueKind() != JsonValueKind.String ||
            action["content"] is not JsonValue chunk ||
            chunk.GetValueKind() != JsonValueKind.String)
        {
            return state;
        }

        var parts = active["responseParts"] as JsonArray;
        if (parts is null)
        {
            return state;
        }

        var pid = partId.GetValue<string>();
        var content = chunk.GetValue<string>();
        var updated = false;
        var newParts = new JsonArray();
        foreach (var partNode in parts)
        {
            if (!updated &&
                partNode is JsonObject part &&
                part["kind"]?.GetValue<string>() == partKind &&
                part["id"]?.GetValue<string>() == pid)
            {
                var p = (JsonObject)part.DeepClone();
                var prev = p["content"] is JsonValue cv && cv.GetValueKind() == JsonValueKind.String
                    ? cv.GetValue<string>()
                    : "";
                p["content"] = prev + content;
                AppendJson(newParts, p);
                updated = true;
            }
            else
            {
                AppendJson(newParts, partNode?.DeepClone());
            }
        }

        if (!updated)
        {
            return state;
        }

        active["responseParts"] = newParts;
        next["activeTurn"] = active;
        return next;
    }

    private static JsonObject ToolCallStart(JsonObject state, JsonObject action)
    {
        var next = Clone(state);
        var active = ActiveTurn(next);
        if (active is null ||
            active["id"]?.GetValue<string>() != action["turnId"]?.GetValue<string>() ||
            action["toolCallId"] is not JsonValue toolCallId ||
            toolCallId.GetValueKind() != JsonValueKind.String)
        {
            return state;
        }

        var parts = active["responseParts"] as JsonArray ?? new JsonArray();
        var newParts = new JsonArray();
        foreach (var p in parts)
        {
            AppendJson(newParts, p?.DeepClone());
        }

        AppendJson(newParts, new JsonObject
        {
            ["kind"] = "toolCall",
            ["toolCall"] = new JsonObject
            {
                ["toolCallId"] = toolCallId.GetValue<string>(),
                ["toolName"] = action["toolName"]?.GetValue<string>() ?? "unknown",
                ["displayName"] = action["displayName"]?.DeepClone(),
                ["intention"] = action["intention"]?.DeepClone(),
                ["contributor"] = action["contributor"]?.DeepClone(),
                ["status"] = "streaming",
                ["success"] = null,
                ["confirmed"] = null,
                ["content"] = null,
                ["toolInput"] = null,
                ["invocationMessage"] = null,
                ["pastTenseMessage"] = null,
            },
        });
        active["responseParts"] = newParts;
        next["activeTurn"] = active;
        return next;
    }

    private static JsonObject UpdateTool(
        JsonObject state,
        JsonObject action,
        Func<JsonObject, JsonObject> updater)
    {
        var next = Clone(state);
        var active = ActiveTurn(next);
        if (active is null ||
            active["id"]?.GetValue<string>() != action["turnId"]?.GetValue<string>() ||
            action["toolCallId"] is not JsonValue toolCallId ||
            toolCallId.GetValueKind() != JsonValueKind.String)
        {
            return state;
        }

        var parts = active["responseParts"] as JsonArray;
        if (parts is null)
        {
            return state;
        }

        var tid = toolCallId.GetValue<string>();
        var found = false;
        var newParts = new JsonArray();
        foreach (var partNode in parts)
        {
            if (!found &&
                partNode is JsonObject part &&
                part["kind"]?.GetValue<string>() == "toolCall" &&
                part["toolCall"] is JsonObject tc &&
                tc["toolCallId"]?.GetValue<string>() == tid)
            {
                var updated = updater((JsonObject)tc.DeepClone());
                AppendJson(newParts, new JsonObject
                {
                    ["kind"] = "toolCall",
                    ["toolCall"] = updated,
                });
                found = true;
            }
            else
            {
                AppendJson(newParts, partNode?.DeepClone());
            }
        }

        if (!found)
        {
            return state;
        }

        active["responseParts"] = newParts;
        next["activeTurn"] = active;
        return next;
    }

    private static JsonObject Usage(JsonObject state, JsonObject action)
    {
        var next = Clone(state);
        var active = ActiveTurn(next);
        if (active is null ||
            active["id"]?.GetValue<string>() != action["turnId"]?.GetValue<string>() ||
            action["usage"] is not JsonObject usage)
        {
            return state;
        }

        active["usage"] = usage.DeepClone();
        next["activeTurn"] = active;
        return next;
    }

    private static JsonObject EndTurn(JsonObject state, JsonObject action, string turnState)
    {
        var next = Clone(state);
        var active = ActiveTurn(next);
        if (active is null ||
            active["id"]?.GetValue<string>() != action["turnId"]?.GetValue<string>())
        {
            return state;
        }

        double durationVal = 0;
        if (action["duration"] is JsonValue dv && dv.GetValueKind() == JsonValueKind.Number)
        {
            if (dv.TryGetValue<double>(out var d))
            {
                durationVal = Math.Max(0, d);
            }
            else if (dv.TryGetValue<long>(out var l))
            {
                durationVal = Math.Max(0, l);
            }
        }

        // Force non-terminal tool calls to cancelled.
        var parts = active["responseParts"] as JsonArray;
        var newParts = new JsonArray();
        if (parts is not null)
        {
            foreach (var partNode in parts)
            {
                if (partNode is JsonObject part &&
                    part["kind"]?.GetValue<string>() == "toolCall" &&
                    part["toolCall"] is JsonObject tc)
                {
                    var tcc = (JsonObject)tc.DeepClone();
                    var st = tcc["status"]?.GetValue<string>();
                    if (st is not ("completed" or "cancelled"))
                    {
                        tcc["status"] = "cancelled";
                        tcc["success"] = false;
                        tcc["reason"] = "skipped";
                    }

                    AppendJson(newParts, new JsonObject
                    {
                        ["kind"] = "toolCall",
                        ["toolCall"] = tcc,
                    });
                }
                else
                {
                    AppendJson(newParts, partNode?.DeepClone());
                }
            }
        }

        var turn = new JsonObject
        {
            ["id"] = active["id"]?.DeepClone(),
            ["startedAt"] = active["startedAt"]?.DeepClone(),
            ["duration"] = durationVal,
            ["message"] = active["message"]?.DeepClone(),
            ["responseParts"] = newParts,
            ["usage"] = active["usage"]?.DeepClone(),
            ["state"] = turnState,
            ["error"] = turnState == "error" ? action["error"]?.DeepClone() : null,
        };
        var turns = next["turns"] as JsonArray ?? new JsonArray();
        var newTurns = new JsonArray();
        foreach (var t in turns)
        {
            AppendJson(newTurns, t?.DeepClone());
        }

        AppendJson(newTurns, turn);
        next["turns"] = newTurns;
        next["activeTurn"] = null;
        next["activity"] = "";
        return next;
    }

    private static JsonObject Truncated(JsonObject state, JsonObject action)
    {
        var next = Clone(state);
        var turnIdNode = action["turnId"];
        var turns = next["turns"] as JsonArray ?? new JsonArray();
        if (turnIdNode is null || turnIdNode.GetValueKind() == JsonValueKind.Null)
        {
            next["turns"] = new JsonArray();
        }
        else
        {
            if (turnIdNode is not JsonValue tv || tv.GetValueKind() != JsonValueKind.String)
            {
                return state;
            }

            var turnId = tv.GetValue<string>();
            var idx = -1;
            for (var i = 0; i < turns.Count; i++)
            {
                if (turns[i] is JsonObject t && t["id"]?.GetValue<string>() == turnId)
                {
                    idx = i;
                    break;
                }
            }

            if (idx < 0)
            {
                return state;
            }

            var kept = new JsonArray();
            for (var i = 0; i <= idx; i++)
            {
                AppendJson(kept, turns[i]?.DeepClone());
            }

            next["turns"] = kept;
        }

        next["activeTurn"] = null;
        next["activity"] = "";
        return next;
    }

    private static JsonObject ActivityChanged(JsonObject state, JsonObject action)
    {
        var next = Clone(state);
        next["activity"] = action["activity"] is JsonValue av &&
                           av.GetValueKind() == JsonValueKind.String
            ? av.GetValue<string>()
            : "";
        return next;
    }

    private static JsonObject WorkingDirSet(JsonObject state, JsonObject action)
    {
        if (action["directory"] is not JsonValue dir ||
            dir.GetValueKind() != JsonValueKind.String)
        {
            return state;
        }

        var next = Clone(state);
        var directory = dir.GetValue<string>();
        var dirs = next["workingDirectories"] as JsonArray ?? new JsonArray();
        var newDirs = new JsonArray();
        var found = false;
        foreach (var d in dirs)
        {
            if (d is JsonValue dv && dv.GetValueKind() == JsonValueKind.String &&
                dv.GetValue<string>() == directory)
            {
                found = true;
            }

            AppendJson(newDirs, d?.DeepClone());
        }

        if (!found)
        {
            AppendJson(newDirs, JsonValue.Create(directory));
        }

        next["workingDirectories"] = newDirs;
        return next;
    }

    private static JsonObject WorkingDirRemoved(JsonObject state, JsonObject action)
    {
        if (action["directory"] is not JsonValue dir ||
            dir.GetValueKind() != JsonValueKind.String)
        {
            return state;
        }

        var next = Clone(state);
        var directory = dir.GetValue<string>();
        var dirs = next["workingDirectories"] as JsonArray ?? new JsonArray();
        var newDirs = new JsonArray();
        foreach (var d in dirs)
        {
            if (d is JsonValue dv && dv.GetValueKind() == JsonValueKind.String &&
                dv.GetValue<string>() == directory)
            {
                continue;
            }

            AppendJson(newDirs, d?.DeepClone());
        }

        next["workingDirectories"] = newDirs;
        return next;
    }

    /// <summary>Add via the <see cref="JsonNode"/> overload (not generic Add{T}) for trim/AOT.</summary>
    private static void AppendJson(JsonArray array, JsonNode? node) =>
        array.Add((JsonNode?)node);
}
