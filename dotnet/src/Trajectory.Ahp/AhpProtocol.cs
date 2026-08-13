using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Hypabolic.Trajectory.Ahp;

/// <summary>Minimal AHP JSON-RPC framing (protocol pin 0.7.x).</summary>
public static class AhpProtocol
{
    public const string RootChannel = "ahp-root://";
    public const string ProtocolVersion = "0.7.0";
    public const string ClientName = "hypabolic-trajectory-ahp";

    public const string ErrAuthFailed = "ahp_auth_failed";
    public const string ErrAuthRequired = "ahp_auth_required";
    public const string ErrTransport = "ahp_transport_error";
    public const string ErrProtocol = "ahp_protocol_error";
    public const string ErrBackpressure = "ahp_backpressure";
    public const string ErrCancelled = "ahp_cancelled";
    public const string ErrResyncRequired = "ahp_resync_required";

    /// <summary>AOT-safe compact JSON: <see cref="JsonNode.WriteTo"/>, no reflection serialize.</summary>
    private static string WriteCompact(JsonNode node)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream))
        {
            node.WriteTo(writer);
        }

        return Encoding.UTF8.GetString(stream.ToArray());
    }

    public static string EncodeRequest(int id, string method, JsonObject parameters) =>
        WriteCompact(new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id,
            ["method"] = method,
            ["params"] = parameters,
        });

    public static string EncodeNotification(string method, JsonObject parameters) =>
        WriteCompact(new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["method"] = method,
            ["params"] = parameters,
        });

    public static string EncodeResult(JsonNode id, JsonNode? result) =>
        WriteCompact(new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id.DeepClone(),
            ["result"] = result?.DeepClone(),
        });

    public static string EncodeError(JsonNode? id, int code, string message)
    {
        var body = new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["error"] = new JsonObject { ["code"] = code, ["message"] = message },
        };
        if (id is not null) body["id"] = id.DeepClone();
        return WriteCompact(body);
    }

    public static JsonObject ParseMessage(string raw)
    {
        var node = JsonNode.Parse(raw) as JsonObject
            ?? throw new InvalidOperationException(ErrProtocol);
        return node;
    }

    public static JsonObject InitializeParams(string protocolVersion = ProtocolVersion) => new()
    {
        ["channel"] = RootChannel,
        ["protocolVersion"] = protocolVersion,
        ["clientInfo"] = new JsonObject
        {
            ["name"] = ClientName,
            ["version"] = "0.1.2",
        },
    };

    public static JsonObject AuthenticateParams(string token) => new()
    {
        ["channel"] = RootChannel,
        ["token"] = token,
    };

    public static JsonObject SubscribeParams(string channel, long? fromSeq = null)
    {
        var p = new JsonObject { ["channel"] = channel };
        if (fromSeq is not null) p["fromSeq"] = fromSeq.Value;
        return p;
    }

    public static JsonObject ResyncParams(string channel) => new() { ["channel"] = channel };

    public static string SafeErrorMessage(string code) => code switch
    {
        ErrAuthFailed => "AHP authentication failed.",
        ErrAuthRequired => "AHP authentication is required.",
        ErrTransport => "AHP transport error.",
        ErrProtocol => "AHP protocol error.",
        ErrBackpressure => "AHP client backpressure limit reached.",
        ErrCancelled => "AHP client cancelled.",
        ErrResyncRequired => "AHP sequence gap requires resync.",
        _ => "AHP client error.",
    };
}
