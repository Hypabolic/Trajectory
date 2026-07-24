namespace Trajectory;

public interface ISourceAdapter
{
    TrajectorySource Source { get; }
    TrajectoryIR Parse(
        string transcript,
        SourceContext? context,
        NormalizationOptions options);
}

public interface IOutputSchemaAdapter
{
    string SchemaId { get; }
    string SchemaVersion { get; }
    string Project(TrajectoryIR trajectory, OutputProjectionOptions? options = null);
}
