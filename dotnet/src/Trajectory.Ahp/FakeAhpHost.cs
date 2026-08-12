using System.Text.Json.Nodes;

namespace Hypabolic.Trajectory.Ahp;

/// <summary>Declarative host behaviour for a single chat channel.</summary>
public sealed class FakeAhpHostScript
{
    public bool RequireAuth { get; init; }
    public string? AcceptToken { get; init; } = "test-token";
    public JsonObject? InitialSnapshot { get; init; }
    public string InitialRevision { get; init; } = "rev-1";
    public IReadOnlyList<JsonObject> InitialActions { get; init; } = Array.Empty<JsonObject>();
}

/// <summary>Programmable fake AHP host for CI (no real network).</summary>
public sealed class FakeAhpHost
{
    private readonly MemoryAhpTransport _transport;
    private readonly FakeAhpHostScript _script;
    private readonly string _chatChannel;
    private bool _closed;

    public int AuthAttempts { get; private set; }
    public int SubscribeCount { get; private set; }
    public int ResyncCount { get; private set; }
    public List<string> ReceivedMethods { get; } = new();

    public FakeAhpHost(MemoryAhpTransport transport, FakeAhpHostScript script, string chatChannel)
    {
        _transport = transport;
        _script = script;
        _chatChannel = chatChannel;
        _transport.SetHandler(OnFrame);
    }

    public void Close()
    {
        _closed = true;
        _transport.Close();
    }

    public void PushAction(JsonObject envelope) =>
        _transport.Send(AhpProtocol.EncodeNotification("action", new JsonObject
        {
            ["channel"] = _chatChannel,
            ["envelope"] = envelope.DeepClone(),
        }));

    public void PushActions(IEnumerable<JsonObject> envelopes)
    {
        foreach (var env in envelopes)
            PushAction(env);
    }

    public void PushSnapshot(JsonObject snapshot, string revision = "rev-push") =>
        _transport.Send(AhpProtocol.EncodeNotification("snapshot", new JsonObject
        {
            ["channel"] = _chatChannel,
            ["revision"] = revision,
            ["snapshot"] = snapshot.DeepClone(),
        }));

    private void OnFrame(string raw)
    {
        if (_closed) return;
        JsonObject msg;
        try { msg = AhpProtocol.ParseMessage(raw); }
        catch { return; }

        var method = msg["method"]?.GetValue<string>();
        var reqId = msg["id"];
        var parameters = msg["params"] as JsonObject ?? new JsonObject();
        if (method is null || reqId is null) return;
        ReceivedMethods.Add(method);

        switch (method)
        {
            case "initialize":
            {
                var result = new JsonObject
                {
                    ["channel"] = AhpProtocol.RootChannel,
                    ["protocolVersion"] = "0.7.0",
                };
                if (_script.RequireAuth)
                    result["authRequired"] = true;
                _transport.Send(AhpProtocol.EncodeResult(reqId, result));
                break;
            }
            case "authenticate":
            {
                AuthAttempts++;
                var token = parameters["token"]?.GetValue<string>();
                if (_script.AcceptToken is not null && token == _script.AcceptToken)
                    _transport.Send(AhpProtocol.EncodeResult(reqId, new JsonObject { ["ok"] = true }));
                else
                    _transport.Send(AhpProtocol.EncodeError(reqId, -32001, "authentication failed"));
                break;
            }
            case "subscribe":
            {
                SubscribeCount++;
                var channel = parameters["channel"]?.GetValue<string>() ?? _chatChannel;
                var result = new JsonObject { ["channel"] = channel };
                if (_script.InitialSnapshot is not null)
                {
                    result["revision"] = _script.InitialRevision;
                    result["snapshot"] = _script.InitialSnapshot.DeepClone();
                }
                if (_script.InitialActions.Count > 0)
                {
                    var arr = new JsonArray();
                    foreach (var a in _script.InitialActions)
                        arr.Add(a.DeepClone());
                    result["actions"] = arr;
                }
                _transport.Send(AhpProtocol.EncodeResult(reqId, result));
                break;
            }
            case "resync":
            {
                ResyncCount++;
                var snap = _script.InitialSnapshot ?? new JsonObject
                {
                    ["ahpProtocolVersion"] = "0.7.0",
                    ["chat"] = new JsonObject
                    {
                        ["id"] = _chatChannel,
                        ["turns"] = new JsonArray(),
                        ["activeTurn"] = null,
                    },
                };
                _transport.Send(AhpProtocol.EncodeResult(reqId, new JsonObject
                {
                    ["channel"] = _chatChannel,
                    ["revision"] = $"resync-{ResyncCount}",
                    ["snapshot"] = snap.DeepClone(),
                }));
                break;
            }
            default:
                _transport.Send(AhpProtocol.EncodeError(reqId, -32601, "method not found"));
                break;
        }
    }
}
