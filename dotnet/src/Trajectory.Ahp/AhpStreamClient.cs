using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Hypabolic.Trajectory.Streaming;

namespace Hypabolic.Trajectory.Ahp;

/// <summary>Auth credentials returned by the injected callback (never stored on stream state).</summary>
public sealed record AhpAuthCredentials(string Token);

/// <summary>Optional auth provider. Challenge is host-supplied and must not be logged into stream diagnostics.</summary>
public delegate AhpAuthCredentials? AhpAuthCallback(JsonObject? challenge);

/// <summary>Client-level events (host transport); distinct from content-safe stream diagnostics.</summary>
public enum AhpClientEventKind
{
    StreamUpdate,
    AuthRequired,
    AuthFailed,
    ResyncRequired,
    Backpressure,
    Disconnected,
    Error,
    Ready,
}

/// <summary>Host-client event. Auth tokens never appear in <see cref="Update"/>.</summary>
public sealed record AhpClientEvent(
    AhpClientEventKind Kind,
    StreamUpdate? Update = null,
    string? Code = null,
    string? Message = null,
    int? Buffered = null);

/// <summary>Options for <see cref="AhpStreamClient"/>.</summary>
public sealed class AhpClientOptions
{
    public required string ChatChannel { get; init; }
    public AhpAuthCallback? Auth { get; init; }
    public StreamOptions? StreamOptions { get; init; }
    public bool AutoResync { get; init; } = true;
    public int MaxBufferedActions { get; init; } = 256;
    public long? FromServerSeq { get; init; }
    public string ProtocolVersion { get; init; } = AhpProtocol.ProtocolVersion;
}

/// <summary>
/// Connect → auth (callback) → subscribe → feed core AHP stream apply.
/// Cancellation leaves the last committed stream cursor valid.
/// </summary>
public sealed class AhpStreamClient
{
    private readonly IAhpTransport _transport;
    private readonly AhpClientOptions _options;
    private readonly Action<AhpClientEvent> _onEvent;
    private StreamState _state;
    private int _nextId = 1;
    private readonly Dictionary<int, string> _pending = new();
    private readonly List<JsonObject> _actionBuffer = new();
    private bool _paused;
    private bool _cancelled;
    private bool _resyncInflight;
    private string? _authTokenHeld;

    public AhpStreamClient(
        IAhpTransport transport,
        AhpClientOptions options,
        Action<AhpClientEvent>? onEvent = null)
    {
        _transport = transport ?? throw new ArgumentNullException(nameof(transport));
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _onEvent = onEvent ?? (_ => { });
        var streamOpts = options.StreamOptions ?? new StreamOptions
        {
            Source = TrajectorySource.Ahp,
            GroupId = options.ChatChannel,
            AhpProtocolVersion = options.ProtocolVersion,
        };
        if (streamOpts.Source != TrajectorySource.Ahp)
        {
            streamOpts = streamOpts with
            {
                Source = TrajectorySource.Ahp,
                GroupId = streamOpts.GroupId ?? options.ChatChannel,
                AhpProtocolVersion = streamOpts.AhpProtocolVersion ?? options.ProtocolVersion,
            };
        }
        else if (streamOpts.GroupId is null)
        {
            streamOpts = streamOpts with
            {
                GroupId = options.ChatChannel,
                AhpProtocolVersion = streamOpts.AhpProtocolVersion ?? options.ProtocolVersion,
            };
        }
        _state = TrajectoryStream.Create(streamOpts);
        _transport.SetHandler(OnFrame);
    }

    public StreamCursor Cursor => _state.Cursor;
    public StreamState State => _state;
    public bool IsCancelled => _cancelled;

    /// <summary>Test helper to force backpressure buffering.</summary>
    public void SetPausedForTest(bool paused) => _paused = paused;

    public void Start()
    {
        if (_cancelled)
        {
            EmitError(AhpProtocol.ErrCancelled);
            return;
        }
        Request("initialize", AhpProtocol.InitializeParams(_options.ProtocolVersion));
    }

    public void Cancel()
    {
        _cancelled = true;
        _actionBuffer.Clear();
        _authTokenHeld = null;
        try { _transport.Close(); } catch { /* ignore */ }
        _onEvent(new AhpClientEvent(
            AhpClientEventKind.Disconnected,
            Code: AhpProtocol.ErrCancelled,
            Message: AhpProtocol.SafeErrorMessage(AhpProtocol.ErrCancelled)));
    }

    public void Resume()
    {
        _paused = false;
        FlushActions();
    }

    private int Request(string method, JsonObject parameters)
    {
        var id = _nextId++;
        _pending[id] = method;
        try
        {
            _transport.Send(AhpProtocol.EncodeRequest(id, method, parameters));
        }
        catch
        {
            EmitError(AhpProtocol.ErrTransport);
        }
        return id;
    }

    private void OnFrame(string raw)
    {
        if (_cancelled) return;
        JsonObject msg;
        try
        {
            msg = AhpProtocol.ParseMessage(raw);
        }
        catch
        {
            EmitError(AhpProtocol.ErrProtocol);
            return;
        }

        if (msg.ContainsKey("method") && !msg.ContainsKey("id"))
        {
            HandleNotification(msg);
            return;
        }
        if (msg.ContainsKey("id"))
        {
            HandleResponse(msg);
            return;
        }
        EmitError(AhpProtocol.ErrProtocol);
    }

    private void HandleResponse(JsonObject msg)
    {
        var idNode = msg["id"];
        int rawId;
        if (idNode is JsonValue jv && jv.TryGetValue<int>(out var i))
            rawId = i;
        else if (idNode is JsonValue jvs && jvs.TryGetValue<string>(out var s) && int.TryParse(s, out var parsed))
            rawId = parsed;
        else
        {
            EmitError(AhpProtocol.ErrProtocol);
            return;
        }

        if (!_pending.Remove(rawId, out var method))
            return;

        if (msg.ContainsKey("error"))
        {
            var errMsg = msg["error"]?["message"]?.GetValue<string>() ?? "";
            var lower = errMsg.ToLowerInvariant();
            if (method == "authenticate" || lower.Contains("auth", StringComparison.Ordinal))
            {
                _authTokenHeld = null;
                _onEvent(new AhpClientEvent(
                    AhpClientEventKind.AuthFailed,
                    Code: AhpProtocol.ErrAuthFailed,
                    Message: AhpProtocol.SafeErrorMessage(AhpProtocol.ErrAuthFailed)));
                return;
            }
            if (method == "initialize" && (lower.Contains("auth", StringComparison.Ordinal) || lower.Contains("unauthor", StringComparison.Ordinal)))
            {
                BeginAuth(null);
                return;
            }
            EmitError(AhpProtocol.ErrProtocol);
            return;
        }

        var result = msg["result"] as JsonObject;
        switch (method)
        {
            case "initialize":
                if (result is not null && result["authRequired"]?.GetValue<bool>() == true)
                {
                    BeginAuth(result["authChallenge"] as JsonObject);
                    return;
                }
                SendSubscribe();
                break;
            case "authenticate":
                _authTokenHeld = null;
                SendSubscribe();
                break;
            case "subscribe":
                _onEvent(new AhpClientEvent(AhpClientEventKind.Ready));
                if (result is not null)
                    IngestSubscribeResult(result);
                break;
            case "resync":
                _resyncInflight = false;
                if (result is not null)
                    ApplyResyncSnapshot(result);
                break;
        }
    }

    private void HandleNotification(JsonObject msg)
    {
        var method = msg["method"]?.GetValue<string>();
        var parameters = msg["params"] as JsonObject ?? new JsonObject();
        if (method is "auth/required" or "authRequired")
        {
            BeginAuth(parameters.Count > 0 ? parameters : null);
            return;
        }
        if (method is "action" or "channel/action")
        {
            var envelope = parameters["envelope"] as JsonObject ?? parameters;
            if (envelope.ContainsKey("action"))
                BufferAction(envelope);
            return;
        }
        if (method is "snapshot" or "channel/snapshot")
            ApplyHostSnapshot(parameters);
    }

    private void BeginAuth(JsonObject? challenge)
    {
        _onEvent(new AhpClientEvent(
            AhpClientEventKind.AuthRequired,
            Code: AhpProtocol.ErrAuthRequired,
            Message: AhpProtocol.SafeErrorMessage(AhpProtocol.ErrAuthRequired)));
        if (_options.Auth is null)
        {
            _onEvent(new AhpClientEvent(
                AhpClientEventKind.AuthFailed,
                Code: AhpProtocol.ErrAuthFailed,
                Message: AhpProtocol.SafeErrorMessage(AhpProtocol.ErrAuthFailed)));
            return;
        }
        AhpAuthCredentials? creds;
        try
        {
            creds = _options.Auth(challenge);
        }
        catch
        {
            _onEvent(new AhpClientEvent(
                AhpClientEventKind.AuthFailed,
                Code: AhpProtocol.ErrAuthFailed,
                Message: AhpProtocol.SafeErrorMessage(AhpProtocol.ErrAuthFailed)));
            return;
        }
        if (creds is null || string.IsNullOrEmpty(creds.Token))
        {
            _onEvent(new AhpClientEvent(
                AhpClientEventKind.AuthFailed,
                Code: AhpProtocol.ErrAuthFailed,
                Message: AhpProtocol.SafeErrorMessage(AhpProtocol.ErrAuthFailed)));
            return;
        }
        _authTokenHeld = creds.Token;
        Request("authenticate", AhpProtocol.AuthenticateParams(creds.Token));
    }

    private void SendSubscribe() =>
        Request("subscribe", AhpProtocol.SubscribeParams(_options.ChatChannel, _options.FromServerSeq));

    private void IngestSubscribeResult(JsonObject result)
    {
        if (result.ContainsKey("snapshot"))
            ApplyHostSnapshot(result);
        if (result["actions"] is JsonArray actions)
        {
            foreach (var item in actions)
            {
                if (item is JsonObject env)
                    BufferAction(env);
            }
            FlushActions();
        }
    }

    private void BufferAction(JsonObject envelope)
    {
        if (_resyncInflight) return;
        if (_actionBuffer.Count >= _options.MaxBufferedActions)
        {
            _paused = true;
            _onEvent(new AhpClientEvent(
                AhpClientEventKind.Backpressure,
                Code: AhpProtocol.ErrBackpressure,
                Message: AhpProtocol.SafeErrorMessage(AhpProtocol.ErrBackpressure),
                Buffered: _actionBuffer.Count));
            return;
        }
        _actionBuffer.Add(envelope);
        if (!_paused)
            FlushActions();
    }

    private void FlushActions()
    {
        if (_cancelled || _resyncInflight || _actionBuffer.Count == 0) return;
        var batch = _actionBuffer.ToList();
        _actionBuffer.Clear();
        var sb = new StringBuilder();
        foreach (var env in batch)
        {
            sb.Append(env.ToJsonString());
            sb.Append('\n');
        }
        var data = Encoding.UTF8.GetBytes(sb.ToString());
        var (state, update) = TrajectoryStream.ApplyAhpActions(_state, data);
        _state = state;
        EmitUpdate(update);
        if (update.Kind == "reset-required" && update.Reset?.Reason == "sequence-gap")
            HandleSequenceGap(update);
    }

    private void ApplyHostSnapshot(JsonObject parameters)
    {
        JsonNode? snapshotObj = parameters["snapshot"] ?? parameters["chat"];
        if (snapshotObj is null && parameters.ContainsKey("ahpProtocolVersion"))
            snapshotObj = parameters;
        if (snapshotObj is not JsonObject materialObj)
            return;

        if (!materialObj.ContainsKey("chat") && materialObj.ContainsKey("turns"))
        {
            materialObj = new JsonObject
            {
                ["ahpProtocolVersion"] = parameters["ahpProtocolVersion"]?.GetValue<string>()
                    ?? _options.ProtocolVersion,
                ["chat"] = materialObj.DeepClone(),
            };
        }
        else if (!materialObj.ContainsKey("ahpProtocolVersion"))
        {
            var copy = materialObj.DeepClone()!.AsObject();
            copy["ahpProtocolVersion"] = parameters["ahpProtocolVersion"]?.GetValue<string>()
                ?? _options.ProtocolVersion;
            materialObj = copy;
        }

        var revision = parameters["revision"]?.GetValue<string>()
            ?? parameters["sourceRevision"]?.GetValue<string>()
            ?? "host-snapshot";
        var material = Encoding.UTF8.GetBytes(materialObj.ToJsonString());
        var (state, update) = TrajectoryStream.ApplyAhpSnapshot(_state, material, revision);
        _state = state;
        EmitUpdate(update);
    }

    private void HandleSequenceGap(StreamUpdate update)
    {
        _onEvent(new AhpClientEvent(
            AhpClientEventKind.ResyncRequired,
            Update: update,
            Code: AhpProtocol.ErrResyncRequired,
            Message: AhpProtocol.SafeErrorMessage(AhpProtocol.ErrResyncRequired)));
        if (!_options.AutoResync) return;
        _resyncInflight = true;
        _actionBuffer.Clear();
        Request("resync", AhpProtocol.ResyncParams(_options.ChatChannel));
    }

    private void ApplyResyncSnapshot(JsonObject result)
    {
        var prior = _state.Cursor;
        var (state, _) = TrajectoryStream.Reset(_state, new StreamResetRequest
        {
            Reason = "sequence-gap",
            PriorCursor = prior,
            SourceRevision = result["revision"]?.GetValue<string>() ?? "resync",
        });
        _state = state;
        ApplyHostSnapshot(result);
        _resyncInflight = false;
    }

    private void EmitUpdate(StreamUpdate update) =>
        _onEvent(new AhpClientEvent(AhpClientEventKind.StreamUpdate, Update: update));

    private void EmitError(string code) =>
        _onEvent(new AhpClientEvent(
            AhpClientEventKind.Error,
            Code: code,
            Message: AhpProtocol.SafeErrorMessage(code)));
}
