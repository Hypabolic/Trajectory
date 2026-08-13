using System.Buffers;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using Hypabolic.Trajectory;
using Hypabolic.Trajectory.Listing;
using Hypabolic.Trajectory.OpenTelemetry;
using Hypabolic.Trajectory.Streaming;

return await ConformanceProgram.RunAsync(args);

internal static class ConformanceProgram
{
    private const string ProtocolVersion = "1";

    private static readonly HashSet<string> StreamOperations = new(StringComparer.Ordinal)
    {
        "stream-sequence",
        "stream-replay",
        "stream-apply-append",
        "stream-apply-snapshot",
        "stream-apply-ahp-actions",
        "stream-apply-ahp-snapshot",
        "stream-finish",
        "stream-reset",
    };

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

        // LS-05: multi-step stream sequence via core ApplyAppend / ApplySnapshot.
        if (StreamOperations.Contains(request.Operation))
        {
            if (!manifest.TryGetProperty("steps", out var steps) ||
                steps.ValueKind != JsonValueKind.Array ||
                steps.GetArrayLength() == 0)
            {
                throw new ProtocolException(
                    $"Stream operation '{request.Operation}' requires a streaming case with steps[].");
            }

            if (request.Operation is "stream-sequence" or "stream-replay")
            {
                try
                {
                    var output = await ExecuteStreamSequenceAsync(caseDirectory, manifest);
                    return new ConformanceResponse(
                        request.Case,
                        request.Operation,
                        "success",
                        output,
                        [],
                        null);
                }
                catch (StreamEngineUnsupportedException ex)
                {
                    return new ConformanceResponse(
                        request.Case,
                        request.Operation,
                        "unsupported",
                        null,
                        [],
                        new FatalError("capability_unsupported", ex.Message));
                }
            }

            return new ConformanceResponse(
                request.Case,
                request.Operation,
                "unsupported",
                null,
                [],
                new FatalError(
                    "capability_unsupported",
                    "Per-step stream apply ops are not implemented yet."));
        }

        if (!manifest.TryGetProperty("operation", out var operations) ||
            operations.ValueKind != JsonValueKind.Object)
        {
            throw new ProtocolException("Case field 'operation' must be an object.");
        }

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

            // Pi / OpenClaw fixtures place agent roots at temp root; others use store/.
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

    private sealed class StreamEngineUnsupportedException : Exception
    {
        public StreamEngineUnsupportedException(string message) : base(message)
        {
        }
    }

    private static async Task<string> ExecuteStreamSequenceAsync(
        string caseDirectory,
        JsonElement manifest)
    {
        if (!manifest.TryGetProperty("steps", out var stepsEl) ||
            stepsEl.ValueKind != JsonValueKind.Array)
        {
            throw new ProtocolException("Stream sequence requires steps[].");
        }

        var state = TrajectoryStream.Create(StreamOptionsFromManifest(manifest));
        var stepResults = new List<object?>();
        foreach (var step in stepsEl.EnumerateArray())
        {
            var stepId = step.TryGetProperty("id", out var idEl) && idEl.ValueKind == JsonValueKind.String
                ? idEl.GetString()!
                : "step";
            var stepInput = step.GetProperty("input");
            var doubleInvoke = !step.TryGetProperty("double_invoke", out var di) ||
                di.ValueKind != JsonValueKind.False;
            var preCursor = state.Cursor;
            var (next, update) = await ApplyStreamStepAsync(state, caseDirectory, stepInput);
            state = next;
            var idempotent = true;
            if (doubleInvoke)
            {
                StreamState after;
                StreamUpdate update2;
                // True-replay: re-supply with the cursor that governed the first apply.
                var stepKind = RequiredString(stepInput, "kind");
                if (stepKind == "append-bytes" &&
                    update.Kind is "updated" or "unchanged")
                {
                    var replayCursor = ParseStreamCursor(stepInput) ?? preCursor;
                    var data = await LoadStepBytesAsync(caseDirectory, stepInput);
                    var sourceRevision = OptionalString(stepInput, "source_revision");
                    (after, update2) = TrajectoryStream.ApplyAppend(
                        state,
                        data,
                        replayCursor,
                        sourceRevision);
                }
                else
                {
                    // AHP steps: re-apply as written (fingerprint idempotence when
                    // the step omits a cursor). Append alone uses pre-apply cursor.
                    (after, update2) = await ApplyStreamStepAsync(state, caseDirectory, stepInput);
                }

                if (update.Kind is "updated" or "unchanged")
                {
                    idempotent = update2.Kind == "unchanged" ||
                        (update2.Kind == "updated" && StreamStateEquivalent(state, after));
                }
                else
                {
                    idempotent = update2.Kind == update.Kind && StreamStateEquivalent(state, after);
                }

                state = after;
            }

            stepResults.Add(new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["id"] = stepId,
                ["update"] = TrajectoryStream.UpdateToDict(update),
                ["idempotent"] = idempotent,
            });
        }

        var payload = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["steps"] = stepResults,
        };
        if (manifest.TryGetProperty("oracle", out var oracle) &&
            oracle.ValueKind == JsonValueKind.Object)
        {
            var wantAppend = oracle.TryGetProperty("append_equals_prefix", out var ae) &&
                ae.ValueKind == JsonValueKind.True;
            var wantPrefix = oracle.TryGetProperty("prefix_re_normalize", out var pr) &&
                pr.ValueKind == JsonValueKind.True;
            var wantAction = oracle.TryGetProperty("action_equals_snapshot", out var aes) &&
                aes.ValueKind == JsonValueKind.True;
            if (wantAppend || wantPrefix || wantAction)
            {
                var section = new Dictionary<string, object?>(StringComparer.Ordinal);
                if (wantAppend || wantPrefix)
                {
                    var oracleState = TrajectoryStream.Create(StreamOptionsFromManifest(manifest));
                    var rev = state.Cursor.SourceRevision ?? "oracle";
                    StreamUpdate snap;
                    (oracleState, snap) = TrajectoryStream.ApplySnapshot(
                        oracleState,
                        state.CommittedPrefix,
                        rev);
                    // When the append path finished (stable→final), mirror finish so
                    // oracle finality matches (LS-08 stable-to-final).
                    if (snap.Kind is "updated" or "unchanged" && state.Finished)
                    {
                        (oracleState, snap) = TrajectoryStream.Finish(oracleState);
                    }

                    var ok = snap.Kind is "updated" or "unchanged" &&
                        OracleSnapshotsMatch(
                            state.Snapshot,
                            snap.Snapshot,
                            state.Cursor,
                            snap.Cursor);
                    if (wantAppend)
                    {
                        section["append_equals_prefix"] = ok;
                    }

                    if (wantPrefix)
                    {
                        section["prefix_re_normalize"] = ok;
                    }
                }

                if (wantAction)
                {
                    var materialName =
                        oracle.TryGetProperty("snapshot_material", out var sm) &&
                        sm.ValueKind == JsonValueKind.String
                            ? sm.GetString()!
                            : "step-snapshot.json";
                    var snapRev =
                        oracle.TryGetProperty("snapshot_source_revision", out var sr) &&
                        sr.ValueKind == JsonValueKind.String
                            ? sr.GetString()!
                            : "ahp-equiv-1";
                    try
                    {
                        var materialPath = Path.GetFullPath(Path.Combine(caseDirectory, materialName));
                        if (!materialPath.StartsWith(
                                Path.GetFullPath(caseDirectory) + Path.DirectorySeparatorChar,
                                StringComparison.Ordinal) &&
                            materialPath != Path.GetFullPath(caseDirectory))
                        {
                            section["action_equals_snapshot"] = false;
                        }
                        else
                        {
                            var material = await File.ReadAllBytesAsync(materialPath);
                            var (_, snap) = TrajectoryStream.ApplyAhpSnapshot(
                                TrajectoryStream.Create(StreamOptionsFromManifest(manifest)),
                                material,
                                snapRev);
                            section["action_equals_snapshot"] =
                                snap.Kind is "updated" or "unchanged" &&
                                ActionSnapshotParity(state.Snapshot, snap.Snapshot);
                        }
                    }
                    catch
                    {
                        section["action_equals_snapshot"] = false;
                    }
                }

                payload["oracle"] = section;
            }
        }

        return SerializeWireObject(payload);
    }

    private static bool ActionSnapshotParity(StreamSnapshot? actionSnap, StreamSnapshot? snapshotSnap)
    {
        if (actionSnap is null || snapshotSnap is null)
        {
            return actionSnap is null && snapshotSnap is null;
        }

        if (actionSnap.Records.Count != snapshotSnap.Records.Count)
        {
            return false;
        }

        for (var i = 0; i < actionSnap.Records.Count; i++)
        {
            var a = actionSnap.Records[i];
            var o = snapshotSnap.Records[i];
            var aId = a.Record.TryGetValue("id", out var aid) ? aid?.ToString() : null;
            var oId = o.Record.TryGetValue("id", out var oid) ? oid?.ToString() : null;
            if (aId != oId || a.Status != o.Status)
            {
                return false;
            }

            var aRole = a.Record.TryGetValue("role", out var ar) ? ar?.ToString() : null;
            var oRole = o.Record.TryGetValue("role", out var orole) ? orole?.ToString() : null;
            if (aRole == "meta" && oRole == "meta")
            {
                continue;
            }

            var aContent = a.Record.TryGetValue("content", out var ac) ? ac?.ToString() : null;
            var oContent = o.Record.TryGetValue("content", out var oc) ? oc?.ToString() : null;
            if (aRole != oRole || aContent != oContent)
            {
                return false;
            }
        }

        return true;
    }

    private static bool OracleSnapshotsMatch(
        StreamSnapshot? appendSnap,
        StreamSnapshot? oracleSnap,
        StreamCursor appendCursor,
        StreamCursor oracleCursor)
    {
        // Missing snapshot (never updated — pure pending) ≡ empty incomplete snapshot.
        var aRecords = appendSnap?.Records ?? Array.Empty<StreamRecord>();
        var oRecords = oracleSnap?.Records ?? Array.Empty<StreamRecord>();
        if (aRecords.Count != oRecords.Count)
        {
            return false;
        }

        for (var i = 0; i < aRecords.Count; i++)
        {
            var a = aRecords[i];
            var o = oRecords[i];
            var aId = a.Record.TryGetValue("id", out var aid) ? aid?.ToString() ?? "" : "";
            var oId = o.Record.TryGetValue("id", out var oid) ? oid?.ToString() ?? "" : "";
            if (aId != oId ||
                a.Status != o.Status ||
                a.ProvisionalId != o.ProvisionalId ||
                a.ReplacesProvisionalId != o.ReplacesProvisionalId ||
                a.FinalizesProvisionalId != o.FinalizesProvisionalId)
            {
                return false;
            }
        }

        var aDiags = appendSnap?.Diagnostics ?? Array.Empty<StreamDiagnostic>();
        var oDiags = oracleSnap?.Diagnostics ?? Array.Empty<StreamDiagnostic>();
        if (aDiags.Count != oDiags.Count)
        {
            return false;
        }

        for (var i = 0; i < aDiags.Count; i++)
        {
            var a = aDiags[i];
            var o = oDiags[i];
            if (a.Code != o.Code ||
                a.Message != o.Message ||
                a.InputLine != o.InputLine ||
                a.RecordIndex != o.RecordIndex ||
                a.Count != o.Count)
            {
                return false;
            }
        }

        var aComplete = appendSnap?.Complete ?? false;
        var oComplete = oracleSnap?.Complete ?? false;
        if (aComplete != oComplete)
        {
            return false;
        }

        return PositionsEqual(appendCursor.Position, oracleCursor.Position) &&
            appendCursor.PrefixSha256 == oracleCursor.PrefixSha256;
    }

    private static string SerializeWireObject(object? value)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using var writer = NewWriter(buffer);
        WriteWireValue(writer, value);
        writer.Flush();
        return Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    private static void WriteWireValue(Utf8JsonWriter writer, object? value)
    {
        switch (value)
        {
            case null:
                writer.WriteNullValue();
                break;
            case string s:
                writer.WriteStringValue(s);
                break;
            case bool b:
                writer.WriteBooleanValue(b);
                break;
            case byte by:
                writer.WriteNumberValue(by);
                break;
            case sbyte sb:
                writer.WriteNumberValue(sb);
                break;
            case short sh:
                writer.WriteNumberValue(sh);
                break;
            case ushort ush:
                writer.WriteNumberValue(ush);
                break;
            case int i:
                writer.WriteNumberValue(i);
                break;
            case uint ui:
                writer.WriteNumberValue(ui);
                break;
            case long l:
                writer.WriteNumberValue(l);
                break;
            case ulong ul:
                writer.WriteNumberValue(ul);
                break;
            case float f:
                writer.WriteNumberValue(f);
                break;
            case double d:
                writer.WriteNumberValue(d);
                break;
            case decimal m:
                writer.WriteNumberValue(m);
                break;
            case IDictionary<string, object?> dict:
                writer.WriteStartObject();
                foreach (var (key, item) in dict)
                {
                    writer.WritePropertyName(key);
                    WriteWireValue(writer, item);
                }

                writer.WriteEndObject();
                break;
            case System.Collections.IEnumerable list when value is not string:
                writer.WriteStartArray();
                foreach (var item in list)
                {
                    WriteWireValue(writer, item);
                }

                writer.WriteEndArray();
                break;
            default:
                writer.WriteStringValue(Convert.ToString(value, CultureInfo.InvariantCulture));
                break;
        }
    }

    private static StreamOptions StreamOptionsFromManifest(JsonElement manifest)
    {
        var source = ParseSource(RequiredString(manifest, "source"));
        var groupId = OptionalString(manifest, "group_id");
        var opts = new StreamOptions { Source = source, GroupId = groupId };
        if (!manifest.TryGetProperty("options", out var options) ||
            options.ValueKind != JsonValueKind.Object)
        {
            return opts;
        }

        if (options.TryGetProperty("delivery", out var delivery) &&
            delivery.ValueKind == JsonValueKind.String)
        {
            opts = opts with
            {
                Delivery = delivery.GetString() switch
                {
                    "snapshot" => StreamDelivery.Snapshot,
                    "delta" => StreamDelivery.Delta,
                    _ => StreamDelivery.Both,
                },
            };
        }

        if (options.TryGetProperty("include_provisional", out var ip) &&
            ip.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            opts = opts with { IncludeProvisional = ip.GetBoolean() };
        }

        if (options.TryGetProperty("require_complete_lines", out var rcl) &&
            rcl.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            opts = opts with { RequireCompleteLines = rcl.GetBoolean() };
        }

        if (options.TryGetProperty("finalize_on_close", out var foc) &&
            foc.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            opts = opts with { FinalizeOnClose = foc.GetBoolean() };
        }

        if (options.TryGetProperty("max_pending_bytes", out var mpb) &&
            mpb.ValueKind == JsonValueKind.Number)
        {
            opts = opts with { MaxPendingBytes = mpb.GetInt64() };
        }

        if (options.TryGetProperty("max_line_bytes", out var mlb) &&
            mlb.ValueKind == JsonValueKind.Number)
        {
            opts = opts with { MaxLineBytes = mlb.GetInt64() };
        }

        if (options.TryGetProperty("ahp_protocol_version", out var apv) &&
            apv.ValueKind == JsonValueKind.String)
        {
            opts = opts with { AhpProtocolVersion = apv.GetString() };
        }

        if (options.TryGetProperty("reset_policy", out var rp) &&
            rp.ValueKind == JsonValueKind.String &&
            rp.GetString() == "auto-reset")
        {
            opts = opts with { ResetPolicy = StreamResetPolicy.AutoReset };
        }

        return opts;
    }

    private static async Task<byte[]> LoadStepBytesAsync(
        string caseDirectory,
        JsonElement input)
    {
        if (input.TryGetProperty("inline_utf8", out var inline) &&
            inline.ValueKind == JsonValueKind.String)
        {
            return Encoding.UTF8.GetBytes(inline.GetString() ?? "");
        }

        var material = RequiredString(input, "material");
        var path = Path.GetFullPath(Path.Combine(caseDirectory, material));
        if (!IsWithin(path, caseDirectory))
        {
            throw new ProtocolException("Step material escapes its case directory.");
        }

        return await File.ReadAllBytesAsync(path);
    }

    private static StreamCursor? ParseStreamCursor(JsonElement input)
    {
        if (!input.TryGetProperty("cursor", out var cursor) ||
            cursor.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var position = cursor.GetProperty("position");
        var kind = OptionalString(position, "kind") ?? "byte";
        StreamPosition streamPosition = kind switch
        {
            "byte" => new BytePosition
            {
                NextByteOffset = position.TryGetProperty("next_byte_offset", out var nbo)
                    ? TrajectoryStream.JsonSafeFromNumber(nbo, nonNegative: true)
                    : 0,
                PendingByteLength = position.TryGetProperty("pending_byte_length", out var pbl)
                    ? TrajectoryStream.JsonSafeFromNumber(pbl, nonNegative: true)
                    : 0,
            },
            "ahp-server-seq" => new AhpServerSeqPosition
            {
                NextServerSeq = position.TryGetProperty("next_server_seq", out var nss)
                    ? TrajectoryStream.JsonSafeFromNumber(nss)
                    : 0,
                LastServerSeq = position.TryGetProperty("last_server_seq", out var lss)
                    ? TrajectoryStream.JsonSafeFromNumber(lss)
                    : 0,
                NextByteOffset = position.TryGetProperty("next_byte_offset", out var abo)
                    ? TrajectoryStream.JsonSafeFromNumber(abo, nonNegative: true)
                    : null,
            },
            "snapshot-revision" => new SnapshotRevisionPosition
            {
                Revision = OptionalString(position, "revision") ?? "",
                ContentSha256 = OptionalString(position, "content_sha256"),
            },
            "hermes-row" => new HermesRowPosition
            {
                DatabaseGeneration = OptionalString(position, "database_generation") ?? "",
                LastRowId = position.TryGetProperty("last_row_id", out var lri) &&
                    lri.ValueKind == JsonValueKind.Number
                    ? TrajectoryStream.JsonSafeFromNumber(lri)
                    : null,
                ChangeToken = OptionalString(position, "change_token"),
            },
            _ => throw new ProtocolException($"Unsupported stream cursor position kind '{kind}'."),
        };

        return new StreamCursor
        {
            Source = RequiredString(cursor, "source"),
            GroupId = RequiredString(cursor, "group_id"),
            Generation = cursor.TryGetProperty("generation", out var gen)
                ? (ulong)TrajectoryStream.JsonSafeFromNumber(gen, nonNegative: true)
                : 0,
            Position = streamPosition,
            SourceRevision = OptionalString(cursor, "source_revision"),
            PrefixSha256 = OptionalString(cursor, "prefix_sha256"),
        };
    }

    private static async Task<(StreamState State, StreamUpdate Update)> ApplyStreamStepAsync(
        StreamState state,
        string caseDirectory,
        JsonElement stepInput)
    {
        var kind = RequiredString(stepInput, "kind");
        var sourceRevision = OptionalString(stepInput, "source_revision");
        var cursor = ParseStreamCursor(stepInput);
        return kind switch
        {
            "append-bytes" => TrajectoryStream.ApplyAppend(
                state,
                await LoadStepBytesAsync(caseDirectory, stepInput),
                cursor,
                sourceRevision),
            "snapshot-bytes" => TrajectoryStream.ApplySnapshot(
                state,
                await LoadStepBytesAsync(caseDirectory, stepInput),
                sourceRevision ?? "",
                cursor),
            "ahp-snapshot" => TrajectoryStream.ApplyAhpSnapshot(
                state,
                await LoadStepBytesAsync(caseDirectory, stepInput),
                sourceRevision ?? "",
                cursor),
            "ahp-actions" => TrajectoryStream.ApplyAhpActions(
                state,
                await LoadStepBytesAsync(caseDirectory, stepInput),
                cursor),
            "finish" => TrajectoryStream.Finish(state),
            "reset" => await ApplyResetStepAsync(state, caseDirectory, stepInput),
            "hermes-export" => TrajectoryStream.ApplyHermesExport(
                state,
                await LoadStepBytesAsync(caseDirectory, stepInput),
                OptionalString(stepInput, "change_token"),
                OptionalString(stepInput, "database_generation") ?? sourceRevision,
                sourceRevision,
                cursor),
            _ => throw new ProtocolException($"Unsupported stream input kind '{kind}'."),
        };
    }

    private static async Task<(StreamState State, StreamUpdate Update)> ApplyResetStepAsync(
        StreamState state,
        string caseDirectory,
        JsonElement stepInput)
    {
        if (!stepInput.TryGetProperty("reset", out var reset) ||
            reset.ValueKind != JsonValueKind.Object)
        {
            throw new ProtocolException("reset step requires reset object.");
        }

        byte[]? material = null;
        if (reset.TryGetProperty("material", out _) || reset.TryGetProperty("inline_utf8", out _))
        {
            material = await LoadStepBytesAsync(caseDirectory, reset);
        }

        var request = new StreamResetRequest
        {
            Reason = RequiredString(reset, "reason"),
            Generation = reset.TryGetProperty("generation", out var gen)
                ? (ulong)TrajectoryStream.JsonSafeFromNumber(gen, nonNegative: true)
                : null,
            SourceRevision = OptionalString(reset, "source_revision"),
            Material = material,
        };
        return TrajectoryStream.Reset(state, request);
    }

    private static bool StreamStateEquivalent(StreamState a, StreamState b) =>
        a.Finished == b.Finished &&
        a.Generation == b.Generation &&
        a.CommittedPrefix.AsSpan().SequenceEqual(b.CommittedPrefix) &&
        a.PendingBytes.AsSpan().SequenceEqual(b.PendingBytes) &&
        a.Cursor.Source == b.Cursor.Source &&
        a.Cursor.GroupId == b.Cursor.GroupId &&
        a.Cursor.Generation == b.Cursor.Generation &&
        a.Cursor.SourceRevision == b.Cursor.SourceRevision &&
        a.Cursor.PrefixSha256 == b.Cursor.PrefixSha256 &&
        PositionsEqual(a.Cursor.Position, b.Cursor.Position) &&
        a.AhpLastServerSeq == b.AhpLastServerSeq &&
        a.AhpLastSnapshotRevision == b.AhpLastSnapshotRevision &&
        a.AhpLastContentSha256 == b.AhpLastContentSha256;

    private static bool PositionsEqual(StreamPosition a, StreamPosition b) =>
        (a, b) switch
        {
            (BytePosition ba, BytePosition bb) =>
                ba.NextByteOffset == bb.NextByteOffset &&
                ba.PendingByteLength == bb.PendingByteLength,
            (AhpServerSeqPosition aa, AhpServerSeqPosition ab) =>
                aa.NextServerSeq == ab.NextServerSeq &&
                aa.LastServerSeq == ab.LastServerSeq &&
                aa.NextByteOffset == ab.NextByteOffset,
            (SnapshotRevisionPosition sa, SnapshotRevisionPosition sb) =>
                sa.Revision == sb.Revision &&
                sa.ContentSha256 == sb.ContentSha256,
            (HermesRowPosition ha, HermesRowPosition hb) =>
                ha.DatabaseGeneration == hb.DatabaseGeneration &&
                ha.LastRowId == hb.LastRowId &&
                ha.ChangeToken == hb.ChangeToken,
            _ => false,
        };

    private static SourceContext ReadSourceContext(JsonElement element) => new()
    {
        GroupId = OptionalString(element, "group_id"),
        BaseByteOffset = element.TryGetProperty("base_byte_offset", out var offset)
            ? offset.GetInt64()
            : null,
        Partial = element.TryGetProperty("partial", out var partial) && partial.GetBoolean(),
        IncludeEncryptedReasoning = ReadIncludeEncryptedReasoning(element),
    };

    private static bool ReadIncludeEncryptedReasoning(JsonElement element)
    {
        if (!element.TryGetProperty("include_encrypted_reasoning", out var value))
            return false;
        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.String => string.Equals(
                value.GetString(),
                "true",
                StringComparison.OrdinalIgnoreCase),
            _ => false,
        };
    }

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
        "ahp" => TrajectorySource.Ahp,
        "grok-build" => TrajectorySource.GrokBuild,
        "cursor" => TrajectorySource.Cursor,
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
