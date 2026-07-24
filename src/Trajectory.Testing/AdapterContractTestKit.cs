using System.Text;

namespace Hypabolic.Trajectory.Testing;

public sealed record AdapterContractResult
{
    public required string SchemaId { get; init; }
    public required string SchemaVersion { get; init; }
    public required int Utf8Bytes { get; init; }
}

public static class AdapterContractTestKit
{
    public static AdapterContractResult VerifyDeterministicOutput<TOutput>(
        IOutputSchemaAdapter<TOutput> adapter,
        TrajectoryIR trajectory,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(adapter);
        ArgumentNullException.ThrowIfNull(trajectory);
        var first = adapter.Project(trajectory, options);
        var second = adapter.Project(trajectory, options);
        var firstJson = adapter.Serialize(first, options);
        var secondJson = adapter.Serialize(second, options);
        if (!string.Equals(firstJson, secondJson, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Schema '{adapter.SchemaId}' is not deterministic across repeated projection.");
        }

        using var stream = new MemoryStream();
        adapter.Write(stream, first, options);
        var streamed = Encoding.UTF8.GetString(stream.ToArray());
        if (!string.Equals(firstJson, streamed, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Schema '{adapter.SchemaId}' stream output differs from serialized output.");
        }

        return new AdapterContractResult
        {
            SchemaId = adapter.SchemaId,
            SchemaVersion = adapter.SchemaVersion,
            Utf8Bytes = checked((int)stream.Length),
        };
    }
}
