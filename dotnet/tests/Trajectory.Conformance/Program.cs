using System.Buffers;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using Hypabolic.Trajectory;
using Hypabolic.Trajectory.Listing;
using Hypabolic.Trajectory.OpenTelemetry;

return await ConformanceProgram.RunAsync(args);

internal static class ConformanceProgram
{
    private const string ProtocolVersion = "1";

    public static async Task<int> RunAsync(string[] args)
    {
        try
        {
            var requestText = args.Length switch
            {
                0 => await Console.In.ReadToEndAsync(),
                1 => await File.ReadAllTextAsync(args[0]),
                _ => throw new ProtocolException(
                    "Pass one request file or write one request object to stdin."),
            };
            using var requestDocument = JsonDocument.Parse(requestText);
            var request = ReadRequest(requestDocument.RootElement);
            var response = await ExecuteAsync(request);
            WriteResponse(response);
            return response.Status == "protocol-error" ? 2 : 0;
        }
        catch (Exception error) when (
            error is ProtocolException or JsonException or IOException)
        {
            WriteResponse(new ConformanceResponse(
                string.Empty,
                string.Empty,
                "protocol-error",
                null,
                [],
                new FatalError("invalid_request", error.Message)));
            return 2;
        }
    }

    private static ConformanceRequest ReadRequest(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object)
            throw new ProtocolException("The request must be a JSON object.");

        var version = RequiredString(root, "protocol_version");
        if (version != ProtocolVersion)
            throw new ProtocolException($"Unsupported protocol version '{version}'.");

        return new ConformanceRequest(
            RequiredString(root, "case"),
            RequiredString(root, "operation"),
            RequiredString(root, "repository_root"));
    }

    private static async Task<ConformanceResponse> ExecuteAsync(ConformanceRequest request)
    {
        var repositoryRoot = Path.GetFullPath(request.RepositoryRoot);
        var casesRoot = Path.GetFullPath(Path.Combine(repositoryRoot, "conformance", "cases"));
        var caseDirectory = Path.GetFullPath(Path.Combine(casesRoot, request.Case));
        if (!IsWithin(caseDirectory, casesRoot))
            throw new ProtocolException("The case path escapes conformance/cases.");

        var manifestPath = Path.Combine(caseDirectory, "case.json");
        using var manifestDocument = JsonDocument.Parse(
            await File.ReadAllTextAsync(manifestPath));
        var manifest = manifestDocument.RootElement;
        if (!string.Equals(RequiredString(manifest, "id"), request.Case, StringComparison.Ordinal))
            throw new ProtocolException("The requested case does not match its manifest ID.");

        var operations = manifest.GetProperty("operation");
        if (!operations.TryGetProperty(request.Operation, out _))
            throw new ProtocolException(
                $"Case '{request.Case}' does not declare operation '{request.Operation}'.");

        try
        {
            var source = ParseSource(RequiredString(manifest, "source"));
            var engine = TrajectoryEngine.CreateDefault()
                .AddOutputAdapter(new OpenTelemetryGenAiOutputAdapter());
            var diagnostics = Array.Empty<TrajectoryDiagnostic>();
            string output;
            if (request.Operation == "list-trajectories")
            {
                output = await ExecuteListingAsync(
                    engine,
                    source,
                    repositoryRoot,
                    manifest);
            }
            else
            {
                var transcriptName = RequiredString(manifest, "transcript");
                var transcriptPath = Path.GetFullPath(Path.Combine(caseDirectory, transcriptName));
                if (!IsWithin(transcriptPath, caseDirectory))
                    throw new ProtocolException("The transcript path escapes its case directory.");

                var input = new NormalizeInput
                {
                    Source = source,
                    Transcript = await File.ReadAllTextAsync(transcriptPath),
                    SourceContext = ReadSourceContext(manifest.GetProperty("source_context")),
                    Options = ReadOptions(manifest),
                };
                var trajectory = engine.NormalizeToIR(input);
                diagnostics = trajectory.Diagnostics.ToArray();
                output = request.Operation switch
                {
                    "normalize-letta" => LettaEnvelope(
                        engine.ProjectJson(trajectory, OutputSchemaIds.LettaTrajectoryV1),
                        trajectory.Diagnostics),
                    "normalize-canonical" =>
                        engine.ProjectJson(trajectory, OutputSchemaIds.LettaCanonicalV1),
                    "normalize-hypabolic" =>
                        engine.ProjectJson(trajectory, OutputSchemaIds.HypabolicTrajectoryV1),
                    "project-openai" =>
                        engine.ProjectJson(trajectory, OutputSchemaIds.OpenAiChatMessages),
                    "project-minimal-jsonl" =>
                        engine.ProjectJson(trajectory, OutputSchemaIds.JsonlMinimal),
                    "project-otel" =>
                        engine.ProjectJson(trajectory, OutputSchemaIds.OtelGenAiSpansV1),
                    _ => throw new ProtocolException(
                        $"Unsupported operation '{request.Operation}'."),
                };
            }

            return new ConformanceResponse(
                request.Case,
                request.Operation,
                "success",
                output,
                diagnostics,
                null);
        }
        catch (TrajectoryNormalizationException error)
        {
            return new ConformanceResponse(
                request.Case,
                request.Operation,
                "fatal-error",
                null,
                [],
                new FatalError(ErrorCode(error.Code), error.Message));
        }
    }

    private static async Task<string> ExecuteListingAsync(
        TrajectoryEngine engine,
        TrajectorySource source,
        string repositoryRoot,
        JsonElement manifest)
    {
        var storeName = RequiredString(manifest, "store");
        var storePath = Path.GetFullPath(Path.Combine(
            repositoryRoot,
            "conformance",
            "stores",
            storeName,
            "store.json"));
        var storesRoot = Path.GetFullPath(Path.Combine(repositoryRoot, "conformance", "stores"));
        if (!IsWithin(storePath, storesRoot))
            throw new ProtocolException("The store fixture escapes conformance/stores.");

        var temporaryRoot = Path.Combine(
            Path.GetTempPath(),
            $"trajectory-conformance-{Guid.NewGuid():N}");
        Directory.CreateDirectory(temporaryRoot);
        try
        {
            using var storeDocument = JsonDocument.Parse(
                await File.ReadAllTextAsync(storePath));
            foreach (var file in storeDocument.RootElement.GetProperty("files").EnumerateArray())
            {
                var relativePath = RequiredString(file, "path");
                var destination = Path.GetFullPath(Path.Combine(temporaryRoot, relativePath));
                if (!IsWithin(destination, temporaryRoot))
                    throw new ProtocolException("A store file escapes the temporary root.");

                Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                await File.WriteAllTextAsync(destination, RequiredString(file, "content"));
                if (file.TryGetProperty("updated_at", out var updatedAt))
                {
                    File.SetLastWriteTimeUtc(
                        destination,
                        DateTime.Parse(
                            updatedAt.GetString()!,
                            CultureInfo.InvariantCulture,
                            DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal));
                }
            }

            var listingRoot = source is TrajectorySource.Pi or TrajectorySource.OpenClaw
                ? temporaryRoot
                : Path.Combine(temporaryRoot, "store");
            var hasListing = manifest.TryGetProperty("listing", out var listing);
            var limit = hasListing && listing.TryGetProperty("limit", out var limitElement)
                ? limitElement.GetInt32()
                : 50;
            var allPages = hasListing &&
                listing.TryGetProperty("all_pages", out var allPagesElement) &&
                allPagesElement.GetBoolean();
            var pages = new List<TrajectoryListingPage>();
            string? cursor = null;
            do
            {
                var page = await engine.ListTrajectoriesAsync(new ListTrajectoriesOptions
                {
                    Source = source,
                    Root = listingRoot,
                    Limit = limit,
                    Cursor = cursor,
                });
                pages.Add(page);
                cursor = page.NextCursor;
            }
            while (allPages && cursor is not null);

            return ListingJson(pages, temporaryRoot, allPages);
        }
        finally
        {
            if (Directory.Exists(temporaryRoot))
                Directory.Delete(temporaryRoot, recursive: true);
        }
    }

    private static SourceContext ReadSourceContext(JsonElement element) => new()
    {
        GroupId = OptionalString(element, "group_id"),
        BaseByteOffset = element.TryGetProperty("base_byte_offset", out var offset)
            ? offset.GetInt64()
            : null,
        Partial = element.TryGetProperty("partial", out var partial) && partial.GetBoolean(),
    };

    private static NormalizeOptions ReadOptions(JsonElement manifest)
    {
        var bounds = manifest.GetProperty("bounds");
        var filters = manifest.GetProperty("filters");
        return new NormalizeOptions
        {
            Bounds = new NormalizationBounds
            {
                ToolArguments = bounds.TryGetProperty("tool_arguments", out var arguments)
                    ? new ToolArgumentBounds
                    {
                        MaxCharacters = NullableInt(arguments, "max_characters"),
                    }
                    : null,
                ToolResults = bounds.TryGetProperty("tool_results", out var results)
                    ? new ToolResultBounds
                    {
                        MaxCharacters = NullableInt(results, "max_characters"),
                        Strategy = OptionalString(results, "strategy") == "head"
                            ? ToolResultTruncationStrategy.Head
                            : ToolResultTruncationStrategy.HeadTail,
                    }
                    : null,
            },
            Filters = filters.TryGetProperty("tool_results", out var toolResults)
                ? new NormalizationFilters
                {
                    ToolResults = toolResults.GetString() == "omit"
                        ? ToolResultPolicy.Omit
                        : ToolResultPolicy.Include,
                }
                : null,
        };
    }

    private static int? NullableInt(JsonElement element, string name)
    {
        if (!element.TryGetProperty(name, out var value) ||
            value.ValueKind == JsonValueKind.Null)
            return null;
        return value.GetInt32();
    }

    private static string LettaEnvelope(
        string recordsJson,
        IReadOnlyList<TrajectoryDiagnostic> diagnostics)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using var writer = NewWriter(buffer);
        writer.WriteStartObject();
        writer.WritePropertyName("records");
        writer.WriteRawValue(recordsJson);
        WriteDiagnostics(writer, diagnostics);
        writer.WriteEndObject();
        writer.Flush();
        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static string ListingJson(
        IReadOnlyList<TrajectoryListingPage> pages,
        string temporaryRoot,
        bool allPages)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using var writer = NewWriter(buffer);
        if (allPages)
            writer.WriteStartArray();
        WriteListingPage(writer, pages[0], temporaryRoot);
        if (allPages)
        {
            for (var index = 1; index < pages.Count; index++)
                WriteListingPage(writer, pages[index], temporaryRoot);
            writer.WriteEndArray();
        }
        writer.Flush();
        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static void WriteListingPage(
        Utf8JsonWriter writer,
        TrajectoryListingPage page,
        string temporaryRoot)
    {
        writer.WriteStartObject();
        writer.WriteStartArray("items");
        foreach (var item in page.Items)
        {
            writer.WriteStartObject();
            writer.WriteString("id", item.Id);
            writer.WriteString(
                "path",
                "$ROOT" + item.Path[temporaryRoot.Length..].Replace('\\', '/'));
            if (item.UpdatedAt is { } updatedAt)
                writer.WriteString(
                    "updated_at",
                    updatedAt.UtcDateTime.ToString(
                        "yyyy-MM-dd'T'HH:mm:ss.fff'Z'",
                        CultureInfo.InvariantCulture));
            if (item.Title is not null) writer.WriteString("title", item.Title);
            if (item.SizeBytes is { } size) writer.WriteNumber("size_bytes", size);
            writer.WriteEndObject();
        }
        writer.WriteEndArray();
        if (page.NextCursor is null) writer.WriteNull("next_cursor");
        else writer.WriteString("next_cursor", page.NextCursor);
        writer.WriteEndObject();
    }

    private static void WriteResponse(ConformanceResponse response)
    {
        using var writer = NewWriter(Console.OpenStandardOutput());
        writer.WriteStartObject();
        writer.WriteString("protocol_version", ProtocolVersion);
        writer.WriteString("case", response.Case);
        writer.WriteString("operation", response.Operation);
        writer.WriteString("status", response.Status);
        if (response.OutputText is null) writer.WriteNull("output_text");
        else writer.WriteString("output_text", response.OutputText);
        WriteDiagnostics(writer, response.Diagnostics);
        if (response.FatalError is null)
        {
            writer.WriteNull("fatal_error");
        }
        else
        {
            writer.WriteStartObject("fatal_error");
            writer.WriteString("code", response.FatalError.Code);
            writer.WriteString("message", response.FatalError.Message);
            writer.WriteEndObject();
        }
        writer.WriteEndObject();
        writer.Flush();
    }

    private static void WriteDiagnostics(
        Utf8JsonWriter writer,
        IReadOnlyList<TrajectoryDiagnostic> diagnostics)
    {
        writer.WriteStartArray("diagnostics");
        foreach (var diagnostic in diagnostics)
        {
            writer.WriteStartObject();
            writer.WriteString("code", diagnostic.Code);
            writer.WriteString("message", diagnostic.Message);
            if (diagnostic.InputLine is { } inputLine)
                writer.WriteNumber("inputLine", inputLine);
            if (diagnostic.RecordIndex is { } recordIndex)
                writer.WriteNumber("recordIndex", recordIndex);
            if (diagnostic.Count is { } count)
                writer.WriteNumber("count", count);
            writer.WriteEndObject();
        }
        writer.WriteEndArray();
    }

    private static Utf8JsonWriter NewWriter(IBufferWriter<byte> output) =>
        new(output, WriterOptions());

    private static Utf8JsonWriter NewWriter(Stream output) =>
        new(output, WriterOptions());

    private static JsonWriterOptions WriterOptions() => new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        Indented = false,
    };

    private static TrajectorySource ParseSource(string source) => source switch
    {
        "pi" => TrajectorySource.Pi,
        "claude-code" => TrajectorySource.ClaudeCode,
        "codex" => TrajectorySource.Codex,
        "openclaw" => TrajectorySource.OpenClaw,
        "hermes" => TrajectorySource.Hermes,
        _ => throw new ProtocolException($"Unsupported conformance source '{source}'."),
    };

    private static string ErrorCode(NormalizationErrorCode code) => code switch
    {
        NormalizationErrorCode.InvalidInput => "invalid_input",
        NormalizationErrorCode.UnknownSource => "unknown_source",
        NormalizationErrorCode.UnknownOutputSchema => "unknown_output_schema",
        NormalizationErrorCode.MissingUserRecords => "missing_user_records",
        NormalizationErrorCode.MissingAssistantRecords => "missing_assistant_records",
        NormalizationErrorCode.InvalidNormalizedTranscript => "invalid_normalized_transcript",
        NormalizationErrorCode.ListingUnavailable => "listing_unavailable",
        NormalizationErrorCode.SourceGroupConflict => "source_group_conflict",
        NormalizationErrorCode.SourceGroupRequired => "source_group_required",
        _ => throw new ArgumentOutOfRangeException(nameof(code)),
    };

    private static bool IsWithin(string path, string root) =>
        path.StartsWith(
            root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar,
            StringComparison.Ordinal) ||
        string.Equals(path, root, StringComparison.Ordinal);

    private static string RequiredString(JsonElement element, string name)
    {
        if (!element.TryGetProperty(name, out var value) ||
            value.ValueKind != JsonValueKind.String ||
            string.IsNullOrEmpty(value.GetString()))
            throw new ProtocolException($"'{name}' must be a non-empty string.");
        return value.GetString()!;
    }

    private static string? OptionalString(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) &&
        value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private sealed record ConformanceRequest(
        string Case,
        string Operation,
        string RepositoryRoot);

    private sealed record ConformanceResponse(
        string Case,
        string Operation,
        string Status,
        string? OutputText,
        IReadOnlyList<TrajectoryDiagnostic> Diagnostics,
        FatalError? FatalError);

    private sealed record FatalError(string Code, string Message);

    private sealed class ProtocolException(string message) : Exception(message);
}
