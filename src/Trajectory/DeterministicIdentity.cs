using System.Buffers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace Hypabolic.Trajectory;

internal static class DeterministicIdentity
{
    public static string Sha256Hex(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    public static string Sha256Hex(ReadOnlySpan<byte> value) =>
        Convert.ToHexString(SHA256.HashData(value)).ToLowerInvariant();

    public static string RecordId(string groupId, string stableSourceRecordId, string componentKey) =>
        HashJson(writer =>
        {
            writer.WriteStartArray();
            writer.WriteStringValue(groupId);
            writer.WriteStringValue(stableSourceRecordId);
            writer.WriteStringValue(componentKey);
            writer.WriteEndArray();
        });

    public static string HashJson(Action<Utf8JsonWriter> write)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using (var writer = new Utf8JsonWriter(buffer, new JsonWriterOptions
        {
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            Indented = false,
        }))
        {
            write(writer);
        }

        return Sha256Hex(buffer.WrittenSpan);
    }

    public static string StableGroupId(TrajectorySource source, ReadOnlySpan<byte> transcriptUtf8) =>
        $"derived:{source.ToString().ToLowerInvariant()}:{Sha256Hex(transcriptUtf8)}";

    public static string LocationId(string groupId, SourceAnchorKind anchorKind, long sourceOffset) =>
        Sha256Hex($"{groupId}|{anchorKind.ToString().ToLowerInvariant()}|{sourceOffset}");
}
