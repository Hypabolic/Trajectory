using System.Diagnostics;
using OpenTelemetry;
using OpenTelemetry.Trace;

namespace Hypabolic.Trajectory.OpenTelemetry;

public static class TrajectoryOpenTelemetryExtensions
{
    public const string ActivitySourceName = OtelGenAiConventions.InstrumentationScope;

    public static TracerProviderBuilder AddTrajectoryGenAi(
        this TracerProviderBuilder builder)
    {
        ArgumentNullException.ThrowIfNull(builder);
        return builder.AddSource(ActivitySourceName);
    }

    public static TracerProviderBuilder AddTrajectoryGenAiOtlp(
        this TracerProviderBuilder builder,
        Action<global::OpenTelemetry.Exporter.OtlpExporterOptions>? configure = null)
    {
        ArgumentNullException.ThrowIfNull(builder);
        builder.AddSource(ActivitySourceName);
        return configure is null
            ? builder.AddOtlpExporter()
            : builder.AddOtlpExporter(configure);
    }
}

public sealed class OpenTelemetryGenAiActivityEmitter : IDisposable
{
    private readonly ActivitySource _source;
    private readonly bool _ownsSource;

    public OpenTelemetryGenAiActivityEmitter(ActivitySource? source = null)
    {
        _ownsSource = source is null;
        _source = source ?? new ActivitySource(
            TrajectoryOpenTelemetryExtensions.ActivitySourceName,
            TrajectoryVersion.Current);
    }

    public void Dispose()
    {
        if (_ownsSource)
        {
            _source.Dispose();
        }
    }

    public int Emit(OtelGenAiSpanSetV1 spanSet)
    {
        ArgumentNullException.ThrowIfNull(spanSet);
        var emitted = 0;
        foreach (var root in spanSet.Spans.Where(static span => span.ParentSpanId is null))
        {
            using var rootActivity = Start(root, default);
            if (rootActivity is null)
            {
                continue;
            }

            emitted++;
            foreach (var child in spanSet.Spans.Where(
                         span => string.Equals(span.ParentSpanId, root.SpanId, StringComparison.Ordinal)))
            {
                using var childActivity = Start(child, rootActivity.Context);
                if (childActivity is not null)
                {
                    emitted++;
                    childActivity.SetEndTime(child.EndTime.UtcDateTime);
                }
            }

            rootActivity.SetEndTime(root.EndTime.UtcDateTime);
        }

        return emitted;
    }

    private Activity? Start(OtelGenAiSpanV1 span, ActivityContext parent)
    {
        var tags = new ActivityTagsCollection();
        foreach (var attribute in span.Attributes)
        {
            tags[attribute.Key] = attribute.StringValue ??
                (object?)attribute.IntegerValue ??
                attribute.StringValues?.ToArray();
        }

        tags["otel.schema_url"] = OtelGenAiConventions.SchemaUrl;
        tags["hypabolic.projected.trace_id"] = span.TraceId;
        tags["hypabolic.projected.span_id"] = span.SpanId;
        var links = span.Links.Select(static link => new ActivityLink(new ActivityContext(
            ActivityTraceId.CreateFromString(link.TraceId),
            ActivitySpanId.CreateFromString(link.SpanId),
            ActivityTraceFlags.Recorded)));
        var activity = _source.StartActivity(
            span.Name,
            span.Kind == "CLIENT" ? ActivityKind.Client : ActivityKind.Internal,
            parent,
            tags,
            links,
            span.StartTime);
        if (activity is not null && span.Status == "ERROR")
        {
            activity.SetStatus(ActivityStatusCode.Error);
        }

        return activity;
    }
}
