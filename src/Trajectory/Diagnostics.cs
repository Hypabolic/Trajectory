namespace Trajectory;

public enum DiagnosticSeverity
{
    Info,
    Warning,
    Error
}

/// <summary>
/// A data-safe diagnostic. Messages describe the failure class and never contain transcript values.
/// </summary>
public sealed record TrajectoryDiagnostic(
    string Code,
    DiagnosticSeverity Severity,
    string Message,
    int? Line = null,
    string? Path = null);

public sealed record NormalizationResult(
    TrajectoryIR? IR,
    IReadOnlyList<TrajectoryDiagnostic> Diagnostics)
{
    public TrajectoryIR? Trajectory => IR;
    public bool HasErrors => Diagnostics.Any(static d => d.Severity == DiagnosticSeverity.Error);
}
