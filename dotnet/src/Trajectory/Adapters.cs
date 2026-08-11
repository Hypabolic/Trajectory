using Hypabolic.Trajectory.Internal;

namespace Hypabolic.Trajectory;

internal interface ISourceAdapter
{
    TrajectorySource Source { get; }
    DecodedSession Decode(ReadOnlyMemory<byte> transcriptUtf8, SourceContext sourceContext);
}

public interface IOutputSchemaAdapter
{
    string SchemaId { get; }
    string SchemaVersion { get; }
    Type OutputType { get; }
    object ProjectUntyped(TrajectoryIR trajectory, OutputProjectionOptions? options = null);
    string SerializeUntyped(object output, OutputProjectionOptions? options = null);
    void WriteUntyped(
        Stream destination,
        object output,
        OutputProjectionOptions? options = null);
}

public interface IOutputSchemaAdapter<TOutput> : IOutputSchemaAdapter
{
    TOutput Project(TrajectoryIR trajectory, OutputProjectionOptions? options = null);
    string Serialize(TOutput output, OutputProjectionOptions? options = null);
    void Write(
        Stream destination,
        TOutput output,
        OutputProjectionOptions? options = null);
}

public abstract class OutputSchemaAdapter<TOutput> : IOutputSchemaAdapter<TOutput>
{
    public abstract string SchemaId { get; }
    public abstract string SchemaVersion { get; }
    public Type OutputType => typeof(TOutput);

    public abstract TOutput Project(TrajectoryIR trajectory, OutputProjectionOptions? options = null);
    public abstract string Serialize(TOutput output, OutputProjectionOptions? options = null);

    public virtual void Write(
        Stream destination,
        TOutput output,
        OutputProjectionOptions? options = null)
    {
        ArgumentNullException.ThrowIfNull(destination);
        var json = Serialize(output, options);
        destination.Write(System.Text.Encoding.UTF8.GetBytes(json));
    }

    object IOutputSchemaAdapter.ProjectUntyped(
        TrajectoryIR trajectory,
        OutputProjectionOptions? options) => Project(trajectory, options)!;

    string IOutputSchemaAdapter.SerializeUntyped(
        object output,
        OutputProjectionOptions? options)
    {
        if (output is not TOutput typed)
        {
            throw new ArgumentException(
                $"Output must be assignable to {typeof(TOutput).FullName}.",
                nameof(output));
        }

        return Serialize(typed, options);
    }

    void IOutputSchemaAdapter.WriteUntyped(
        Stream destination,
        object output,
        OutputProjectionOptions? options)
    {
        if (output is not TOutput typed)
        {
            throw new ArgumentException(
                $"Output must be assignable to {typeof(TOutput).FullName}.",
                nameof(output));
        }

        Write(destination, typed, options);
    }
}

public interface ITrajectorySourceAdapter
{
    TrajectorySource Source { get; }
    TrajectoryIR Normalize(NormalizeInput input);
}
