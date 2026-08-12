using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Hypabolic.Trajectory.Streaming;
using Microsoft.Data.Sqlite;

namespace Hypabolic.Trajectory.Hermes;

/// <summary>Host-side provider error (not a stream diagnostic).</summary>
public sealed class HermesHostException : Exception
{
    public const string StoreRequired = "store_required";
    public const string SessionNotFound = "session_not_found";
    public const string DbError = "db_error";

    public string Code { get; }

    public HermesHostException(string code, string message, Exception? inner = null)
        : base(message, inner)
    {
        Code = code;
    }
}

/// <summary>Session listing row from a Hermes store.</summary>
public sealed record HermesSessionInfo(
    string SessionId,
    string? Title = null,
    string? Model = null,
    double? StartedAt = null,
    string? Source = null);

/// <summary>Provider storage: list sessions, export JSON, generation token.</summary>
public interface IHermesStore
{
    IReadOnlyList<HermesSessionInfo> ListSessions();
    byte[] ExportSession(string sessionId);
    string DatabaseGeneration { get; }
}

/// <summary>Compute opaque change token over ordered active messages.</summary>
public static class HermesChangeToken
{
    public static string Compute(JsonArray messages)
    {
        var active = OrderActive(messages);
        var parts = new List<string>();
        foreach (var row in active)
        {
            var subset = new JsonObject
            {
                ["id"] = row["id"]?.DeepClone(),
                ["role"] = row["role"]?.DeepClone(),
                ["content"] = row["content"]?.DeepClone(),
                ["tool_call_id"] = row["tool_call_id"]?.DeepClone(),
                ["tool_name"] = row["tool_name"]?.DeepClone(),
                ["tool_calls"] = row["tool_calls"]?.DeepClone(),
                ["finish_reason"] = row["finish_reason"]?.DeepClone(),
                ["timestamp"] = row["timestamp"]?.DeepClone(),
                ["active"] = row["active"]?.DeepClone() ?? 1,
            };
            parts.Add(Sha256Hex(Encoding.UTF8.GetBytes(subset.ToJsonString())));
        }

        return Sha256Hex(Encoding.UTF8.GetBytes(parts.Count == 0 ? "" : string.Join("|", parts)));
    }

    public static byte[] ExportJson(JsonObject? session, JsonArray messages)
    {
        var active = OrderActive(messages);
        JsonNode payload = session is null
            ? new JsonObject { ["messages"] = active }
            : new JsonObject
            {
                ["session"] = session.DeepClone(),
                ["messages"] = active,
            };
        return Encoding.UTF8.GetBytes(payload.ToJsonString());
    }

    internal static JsonArray OrderActive(JsonArray messages)
    {
        var active = new List<JsonObject>();
        foreach (var item in messages)
        {
            if (item is not JsonObject row)
            {
                continue;
            }

            if (IsInactive(row))
            {
                continue;
            }

            active.Add(row);
        }

        if (active.Count > 0 && active.All(r => r["id"] is JsonValue jv && jv.TryGetValue<long>(out _)))
        {
            active = active
                .Select((r, i) => (r, i, id: r["id"]!.GetValue<long>()))
                .OrderBy(t => t.id)
                .ThenBy(t => t.i)
                .Select(t => t.r)
                .ToList();
        }

        var arr = new JsonArray();
        foreach (var row in active)
        {
            arr.Add(row.DeepClone());
        }

        return arr;
    }

    internal static bool IsInactive(JsonObject row)
    {
        var activeFlag = row["active"];
        if (activeFlag is not JsonValue av)
        {
            return false;
        }

        if (av.TryGetValue<int>(out var ai) && ai == 0)
        {
            return true;
        }

        if (av.TryGetValue<bool>(out var ab) && !ab)
        {
            return true;
        }

        return av.TryGetValue<string>(out var as_) && as_ == "0";
    }

    private static string Sha256Hex(byte[] data)
    {
        var hash = SHA256.HashData(data);
        return Convert.ToHexString(hash).ToLowerInvariant();
    }
}

/// <summary>In-memory Hermes store for CI fixtures.</summary>
public sealed class MemoryHermesStore : IHermesStore
{
    private readonly Dictionary<string, JsonObject> _sessions = new(StringComparer.Ordinal);
    private readonly Dictionary<string, List<JsonObject>> _messages = new(StringComparer.Ordinal);
    private long _nextRowId = 1;

    public string DatabaseGeneration { get; set; } = "mem-0";

    public IReadOnlyList<HermesSessionInfo> ListSessions() =>
        _sessions
            .OrderBy(kv => kv.Key, StringComparer.Ordinal)
            .Select(kv => new HermesSessionInfo(
                kv.Key,
                kv.Value["title"]?.GetValue<string>(),
                kv.Value["model"]?.GetValue<string>(),
                kv.Value["started_at"]?.GetValue<double>(),
                kv.Value["source"]?.GetValue<string>()))
            .ToList();

    public byte[] ExportSession(string sessionId)
    {
        if (!_sessions.ContainsKey(sessionId) && !_messages.ContainsKey(sessionId))
        {
            throw new HermesHostException(
                HermesHostException.SessionNotFound,
                "Hermes session was not found in the provider store.");
        }

        var session = _sessions.GetValueOrDefault(sessionId);
        var messages = new JsonArray();
        foreach (var m in _messages.GetValueOrDefault(sessionId) ?? [])
        {
            messages.Add(m.DeepClone());
        }

        return HermesChangeToken.ExportJson(session, messages);
    }

    public void UpsertSession(JsonObject session)
    {
        var sid = session["id"]?.GetValue<string>();
        if (string.IsNullOrEmpty(sid))
        {
            throw new HermesHostException(HermesHostException.DbError, "Hermes provider could not query the store.");
        }

        _sessions[sid] = (JsonObject)session.DeepClone();
    }

    public JsonObject AppendMessage(string sessionId, JsonObject row)
    {
        var msg = (JsonObject)row.DeepClone();
        if (msg["id"] is null)
        {
            msg["id"] = _nextRowId++;
        }
        else if (msg["id"] is JsonValue jv && jv.TryGetValue<long>(out var id))
        {
            _nextRowId = Math.Max(_nextRowId, id + 1);
        }

        msg["session_id"] ??= sessionId;
        msg["active"] ??= 1;
        if (!_messages.TryGetValue(sessionId, out var list))
        {
            list = [];
            _messages[sessionId] = list;
        }

        list.Add(msg);
        return msg;
    }

    public void SoftDeleteMessage(string sessionId, long messageId)
    {
        if (!_messages.TryGetValue(sessionId, out var list))
        {
            throw new HermesHostException(
                HermesHostException.SessionNotFound,
                "Hermes session was not found in the provider store.");
        }

        foreach (var row in list)
        {
            if (row["id"] is JsonValue jv && TryGetLong(jv, out var id) && id == messageId)
            {
                row["active"] = 0;
                return;
            }
        }

        throw new HermesHostException(
            HermesHostException.SessionNotFound,
            "Hermes session was not found in the provider store.");
    }

    private static bool TryGetLong(JsonValue jv, out long value)
    {
        if (jv.TryGetValue<long>(out value))
        {
            return true;
        }

        if (jv.TryGetValue<int>(out var i))
        {
            value = i;
            return true;
        }

        value = 0;
        return false;
    }
}

/// <summary>SQLite Hermes store: query rows (never byte-tail).</summary>
public sealed class SqliteHermesProvider : IHermesStore
{
    private const string SchemaSql = """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'tui',
            model TEXT,
            title TEXT,
            cwd TEXT,
            system_prompt TEXT,
            started_at REAL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL DEFAULT 0,
            finish_reason TEXT,
            reasoning TEXT,
            reasoning_content TEXT,
            observed INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id);
        """;

    public string Path { get; }
    public string DatabaseGeneration { get; set; }

    public SqliteHermesProvider(string path, string? databaseGeneration = null)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new HermesHostException(
                HermesHostException.StoreRequired,
                "Hermes provider store path is required.");
        }

        Path = System.IO.Path.GetFullPath(path);
        DatabaseGeneration = databaseGeneration ?? $"sqlite:{Path}";
    }

    public void InitializeSchema()
    {
        var dir = System.IO.Path.GetDirectoryName(Path);
        if (!string.IsNullOrEmpty(dir))
        {
            Directory.CreateDirectory(dir);
        }

        try
        {
            using var conn = Open();
            conn.Execute(SchemaSql);
        }
        catch (SqliteException ex)
        {
            throw new HermesHostException(
                HermesHostException.DbError,
                "Hermes provider could not query the store.",
                ex);
        }
    }

    public IReadOnlyList<HermesSessionInfo> ListSessions()
    {
        if (!File.Exists(Path))
        {
            return Array.Empty<HermesSessionInfo>();
        }

        try
        {
            using var conn = Open();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "SELECT id, source, model, title, started_at FROM sessions ORDER BY id";
            using var reader = cmd.ExecuteReader();
            var list = new List<HermesSessionInfo>();
            while (reader.Read())
            {
                list.Add(new HermesSessionInfo(
                    reader.GetString(0),
                    reader.IsDBNull(3) ? null : reader.GetString(3),
                    reader.IsDBNull(2) ? null : reader.GetString(2),
                    reader.IsDBNull(4) ? null : reader.GetDouble(4),
                    reader.IsDBNull(1) ? null : reader.GetString(1)));
            }

            return list;
        }
        catch (SqliteException)
        {
            return Array.Empty<HermesSessionInfo>();
        }
    }

    public byte[] ExportSession(string sessionId)
    {
        if (!File.Exists(Path))
        {
            throw new HermesHostException(
                HermesHostException.SessionNotFound,
                "Hermes session was not found in the provider store.");
        }

        try
        {
            using var conn = Open();
            using var tx = conn.BeginTransaction();
            JsonObject? session = null;
            using (var cmd = conn.CreateCommand())
            {
                cmd.Transaction = tx;
                cmd.CommandText = "SELECT id, source, model, title, cwd, system_prompt, started_at FROM sessions WHERE id = $id";
                cmd.Parameters.AddWithValue("$id", sessionId);
                using var reader = cmd.ExecuteReader();
                if (reader.Read())
                {
                    session = new JsonObject
                    {
                        ["id"] = reader.GetString(0),
                        ["source"] = reader.IsDBNull(1) ? null : reader.GetString(1),
                        ["model"] = reader.IsDBNull(2) ? null : reader.GetString(2),
                        ["title"] = reader.IsDBNull(3) ? null : reader.GetString(3),
                        ["cwd"] = reader.IsDBNull(4) ? null : reader.GetString(4),
                        ["system_prompt"] = reader.IsDBNull(5) ? null : reader.GetString(5),
                        ["started_at"] = reader.IsDBNull(6) ? null : reader.GetDouble(6),
                    };
                }
            }

            var messages = new JsonArray();
            using (var cmd = conn.CreateCommand())
            {
                cmd.Transaction = tx;
                cmd.CommandText = """
                    SELECT id, session_id, role, content, tool_call_id, tool_calls, tool_name,
                           timestamp, finish_reason, reasoning, reasoning_content, observed, active
                    FROM messages WHERE session_id = $id ORDER BY id
                    """;
                cmd.Parameters.AddWithValue("$id", sessionId);
                using var reader = cmd.ExecuteReader();
                while (reader.Read())
                {
                    var row = new JsonObject
                    {
                        ["id"] = reader.GetInt64(0),
                        ["session_id"] = reader.GetString(1),
                        ["role"] = reader.GetString(2),
                        ["content"] = reader.IsDBNull(3) ? null : reader.GetString(3),
                        ["tool_call_id"] = reader.IsDBNull(4) ? null : reader.GetString(4),
                        ["tool_calls"] = reader.IsDBNull(5) ? null : reader.GetString(5),
                        ["tool_name"] = reader.IsDBNull(6) ? null : reader.GetString(6),
                        ["timestamp"] = reader.IsDBNull(7) ? 0 : reader.GetDouble(7),
                        ["finish_reason"] = reader.IsDBNull(8) ? null : reader.GetString(8),
                        ["reasoning"] = reader.IsDBNull(9) ? null : reader.GetString(9),
                        ["reasoning_content"] = reader.IsDBNull(10) ? null : reader.GetString(10),
                        ["observed"] = reader.IsDBNull(11) ? 0 : reader.GetInt32(11),
                        ["active"] = reader.IsDBNull(12) ? 1 : reader.GetInt32(12),
                    };
                    messages.Add(row);
                }
            }

            tx.Commit();
            if (session is null && messages.Count == 0)
            {
                throw new HermesHostException(
                    HermesHostException.SessionNotFound,
                    "Hermes session was not found in the provider store.");
            }

            return HermesChangeToken.ExportJson(session, messages);
        }
        catch (HermesHostException)
        {
            throw;
        }
        catch (Exception ex)
        {
            throw new HermesHostException(
                HermesHostException.DbError,
                "Hermes provider could not query the store.",
                ex);
        }
    }

    public void InsertSession(JsonObject session)
    {
        InitializeSchema();
        var sid = session["id"]?.GetValue<string>();
        if (string.IsNullOrEmpty(sid))
        {
            throw new HermesHostException(HermesHostException.DbError, "Hermes provider could not query the store.");
        }

        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT OR REPLACE INTO sessions (id, source, model, title, cwd, system_prompt, started_at)
            VALUES ($id, $source, $model, $title, $cwd, $system_prompt, $started_at)
            """;
        cmd.Parameters.AddWithValue("$id", sid);
        cmd.Parameters.AddWithValue("$source", session["source"]?.GetValue<string>() ?? "tui");
        cmd.Parameters.AddWithValue("$model", (object?)session["model"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$title", (object?)session["title"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$cwd", (object?)session["cwd"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$system_prompt", (object?)session["system_prompt"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$started_at", (object?)session["started_at"]?.GetValue<double>() ?? DBNull.Value);
        cmd.ExecuteNonQuery();
    }

    public long InsertMessage(string sessionId, JsonObject row)
    {
        InitializeSchema();
        string? toolCalls = null;
        if (row["tool_calls"] is not null)
        {
            toolCalls = row["tool_calls"] is JsonValue jv && jv.TryGetValue<string>(out var s)
                ? s
                : row["tool_calls"]!.ToJsonString();
        }

        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO messages
            (id, session_id, role, content, tool_call_id, tool_calls, tool_name,
             timestamp, finish_reason, reasoning, reasoning_content, observed, active)
            VALUES ($id, $session_id, $role, $content, $tool_call_id, $tool_calls, $tool_name,
                    $timestamp, $finish_reason, $reasoning, $reasoning_content, $observed, $active)
            """;
        object idVal = DBNull.Value;
        if (row["id"] is JsonValue idJv)
        {
            if (idJv.TryGetValue<long>(out var idL))
            {
                idVal = idL;
            }
            else if (idJv.TryGetValue<int>(out var idI))
            {
                idVal = (long)idI;
            }
        }
        cmd.Parameters.AddWithValue("$id", idVal);
        cmd.Parameters.AddWithValue("$session_id", sessionId);
        cmd.Parameters.AddWithValue("$role", row["role"]?.GetValue<string>() ?? "user");
        cmd.Parameters.AddWithValue("$content", (object?)row["content"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$tool_call_id", (object?)row["tool_call_id"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$tool_calls", (object?)toolCalls ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$tool_name", (object?)row["tool_name"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$timestamp", row["timestamp"]?.GetValue<double>() ?? 0);
        cmd.Parameters.AddWithValue("$finish_reason", (object?)row["finish_reason"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$reasoning", (object?)row["reasoning"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$reasoning_content", (object?)row["reasoning_content"]?.GetValue<string>() ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$observed", row["observed"]?.GetValue<int>() ?? 0);
        cmd.Parameters.AddWithValue("$active", HermesChangeToken.IsInactive(row) ? 0 : 1);
        cmd.ExecuteNonQuery();
        using var idCmd = conn.CreateCommand();
        idCmd.CommandText = "SELECT last_insert_rowid()";
        return (long)(idCmd.ExecuteScalar() ?? 0L);
    }

    public void SoftDeleteMessage(string sessionId, long messageId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "UPDATE messages SET active = 0 WHERE session_id = $sid AND id = $id";
        cmd.Parameters.AddWithValue("$sid", sessionId);
        cmd.Parameters.AddWithValue("$id", messageId);
        var n = cmd.ExecuteNonQuery();
        if (n == 0)
        {
            throw new HermesHostException(
                HermesHostException.SessionNotFound,
                "Hermes session was not found in the provider store.");
        }
    }

    private SqliteConnection Open()
    {
        var conn = new SqliteConnection($"Data Source={Path}");
        conn.Open();
        return conn;
    }
}

file static class SqliteExtensions
{
    public static void Execute(this SqliteConnection conn, string sql)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }
}

/// <summary>Options for <see cref="HermesProviderStream"/>.</summary>
public sealed class HermesProviderOptions
{
    public required string SessionId { get; init; }
    public required IHermesStore Store { get; init; }
    public StreamOptions? Stream { get; init; }
    public string? GroupId { get; init; }
}

/// <summary>Poll a Hermes store and feed core ApplyHermesExport.</summary>
public sealed class HermesProviderStream
{
    private readonly IHermesStore _store;
    private readonly string _sessionId;
    private StreamState _state;
    private bool _closed;

    private HermesProviderStream(IHermesStore store, string sessionId, StreamState state)
    {
        _store = store;
        _sessionId = sessionId;
        _state = state;
    }

    public StreamCursor Cursor => _state.Cursor;
    public StreamState State => _state;

    public static HermesProviderStream Open(HermesProviderOptions options)
    {
        ArgumentNullException.ThrowIfNull(options);
        var group = options.GroupId ?? options.SessionId;
        var streamOpts = options.Stream ?? new StreamOptions
        {
            Source = TrajectorySource.Hermes,
            GroupId = group,
        };
        var state = TrajectoryStream.Create(streamOpts);
        return new HermesProviderStream(options.Store, options.SessionId, state);
    }

    public IReadOnlyList<HermesSessionInfo> ListSessions() => _store.ListSessions();

    public StreamUpdate? Poll()
    {
        if (_closed)
        {
            return null;
        }

        var gen = _store.DatabaseGeneration;
        byte[] export;
        try
        {
            export = _store.ExportSession(_sessionId);
        }
        catch (HermesHostException)
        {
            throw;
        }
        catch (Exception ex)
        {
            throw new HermesHostException(
                HermesHostException.DbError,
                "Hermes provider could not query the store.",
                ex);
        }

        JsonNode? parsed;
        try
        {
            parsed = JsonNode.Parse(export);
        }
        catch (Exception ex)
        {
            throw new HermesHostException(
                HermesHostException.DbError,
                "Hermes provider could not query the store.",
                ex);
        }

        JsonArray messages;
        if (parsed is JsonArray arr)
        {
            messages = arr;
        }
        else if (parsed is JsonObject obj && obj["messages"] is JsonArray msgs)
        {
            messages = msgs;
        }
        else
        {
            throw new HermesHostException(
                HermesHostException.DbError,
                "Hermes provider could not query the store.");
        }

        var token = HermesChangeToken.Compute(messages);

        if (_state.Snapshot is not null &&
            _state.Cursor.Position is HermesRowPosition pos &&
            !string.IsNullOrEmpty(pos.DatabaseGeneration) &&
            pos.DatabaseGeneration != gen)
        {
            var (resetState, resetUpdate) = TrajectoryStream.Reset(
                _state,
                new StreamResetRequest
                {
                    Reason = "source-replaced",
                    SourceRevision = gen,
                    Material = export,
                    ChangeToken = token,
                });
            _state = resetState;
            return resetUpdate;
        }

        var (state, update) = TrajectoryStream.ApplyHermesExport(
            _state,
            export,
            token,
            gen,
            gen,
            cursor: null);
        _state = state;
        return update;
    }

    public void Close() => _closed = true;
}
