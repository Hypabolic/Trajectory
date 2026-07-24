using System.Security.Cryptography;
using System.Text;

namespace Trajectory;

/// <summary>Creates culture-independent identities from length-delimited UTF-8 values.</summary>
public static class DeterministicIdentity
{
    public static string Sha256Hex(string value)
    {
        ArgumentNullException.ThrowIfNull(value);
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();
    }

    public static string Create(string prefix, params string?[] parts)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(prefix);
        ArgumentNullException.ThrowIfNull(parts);

        using var stream = new MemoryStream();
        Span<byte> length = stackalloc byte[4];
        foreach (var part in parts)
        {
            var bytes = Encoding.UTF8.GetBytes(part ?? string.Empty);
            System.Buffers.Binary.BinaryPrimitives.WriteInt32BigEndian(length, bytes.Length);
            stream.Write(length);
            stream.Write(bytes);
        }

        var hash = SHA256.HashData(stream.GetBuffer().AsSpan(0, checked((int)stream.Length)));
        return string.Concat(prefix, "_", Convert.ToHexString(hash).ToLowerInvariant());
    }
}
