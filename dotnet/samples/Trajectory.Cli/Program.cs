using System.ComponentModel;
using System.Text.Json;
using System.Text.Json.Nodes;
using Hypabolic.Trajectory;
using Hypabolic.Trajectory.Ahp;
using Hypabolic.Trajectory.IO;
using Hypabolic.Trajectory.Listing;
using Hypabolic.Trajectory.Streaming;
using Spectre.Console;
using Spectre.Console.Cli;

namespace Hypabolic.Trajectory.Cli;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        var app = new CommandApp<InteractiveCommand>();
        app.Configure(config =>
        {
            // Bubble command exceptions to Main so typed fatal codes map to exit 2
            // (invalid_input / stream privacy validation), matching peer sample CLIs.
            config.PropagateExceptions();
            config.SetApplicationName("trajectory");
            config.ValidateExamples();
            config.AddCommand<ListCommand>("list")
                .WithDescription("List discovered local sessions for a source.");
            config.AddCommand<ShowCommand>("show")
                .WithDescription("Normalize a session file and print a privacy-safe summary.");
            config.AddCommand<StreamCommand>("stream")
                .WithDescription("Follow a JSONL session (pick from the store, or --path/--id; not a daemon).");
            config.AddCommand<AhpStreamCommand>("ahp-stream")
                .WithDescription("Demo optional AHP client with fake:// FakeAhpHost (not a daemon).");
            config.AddCommand<InteractiveCommand>("browse")
                .WithDescription("Interactive source → session → snapshot or live follow (default).");
        });

        try
        {
            return await app.RunAsync(args);
        }
        catch (TrajectoryNormalizationException error)
        {
            AnsiConsole.MarkupLine($"[red]{Markup.Escape(error.Code.ToString())}:[/] {Markup.Escape(error.Message)}");
            return 2;
        }
        catch (FileStreamHostException error)
        {
            AnsiConsole.MarkupLine($"[red]{Markup.Escape(error.Code)}:[/] {Markup.Escape(error.Message)}");
            return 2;
        }
        catch (Exception error)
        {
            AnsiConsole.MarkupLine($"[red]error:[/] {Markup.Escape(error.Message)}");
            return 1;
        }
    }
}

internal static class Sources
{
    public static readonly string[] Names =
    [
        "pi",
        "claude-code",
        "codex",
        "openclaw",
        "hermes",
        "ahp",
        "grok-build",
        "cursor",
    ];

    public static TrajectorySource Parse(string value) => value.Trim().ToLowerInvariant() switch
    {
        "pi" => TrajectorySource.Pi,
        "claude-code" or "claudecode" or "claude" => TrajectorySource.ClaudeCode,
        "codex" => TrajectorySource.Codex,
        "openclaw" => TrajectorySource.OpenClaw,
        "hermes" => TrajectorySource.Hermes,
        "ahp" => TrajectorySource.Ahp,
        "grok-build" or "grok" => TrajectorySource.GrokBuild,
        "cursor" or "cursor-agent" => TrajectorySource.Cursor,
        _ => throw new TrajectoryNormalizationException(
            NormalizationErrorCode.UnknownSource,
            $"Unknown source '{value}'. Expected one of: {string.Join(", ", Names)} (aliases: grok, cursor-agent)."),
    };

    public static string WireName(TrajectorySource source) => source switch
    {
        TrajectorySource.Pi => "pi",
        TrajectorySource.ClaudeCode => "claude-code",
        TrajectorySource.Codex => "codex",
        TrajectorySource.OpenClaw => "openclaw",
        TrajectorySource.Hermes => "hermes",
        TrajectorySource.Ahp => "ahp",
        TrajectorySource.GrokBuild => "grok-build",
        TrajectorySource.Cursor => "cursor",
        _ => source.ToString().ToLowerInvariant(),
    };
}

internal static class StoreRoots
{
    public static string Resolve(TrajectorySource source, string? rootOverride)
    {
        if (!string.IsNullOrWhiteSpace(rootOverride))
        {
            return Expand(rootOverride.Trim());
        }

        var envKey = source switch
        {
            TrajectorySource.Pi => "TRAJECTORY_PI_ROOT",
            TrajectorySource.ClaudeCode => "TRAJECTORY_CLAUDE_CODE_ROOT",
            TrajectorySource.Codex => "TRAJECTORY_CODEX_ROOT",
            TrajectorySource.OpenClaw => "TRAJECTORY_OPENCLAW_ROOT",
            TrajectorySource.Hermes => "TRAJECTORY_HERMES_ROOT",
            TrajectorySource.Ahp => "TRAJECTORY_AHP_ROOT",
            TrajectorySource.GrokBuild => "TRAJECTORY_GROK_BUILD_ROOT",
            TrajectorySource.Cursor => "TRAJECTORY_CURSOR_ROOT",
            _ => null,
        };
        if (envKey is not null)
        {
            var fromEnv = Environment.GetEnvironmentVariable(envKey);
            if (!string.IsNullOrWhiteSpace(fromEnv))
            {
                return Expand(fromEnv.Trim());
            }
        }

        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        return source switch
        {
            TrajectorySource.Pi => FirstNonEmpty(
                Environment.GetEnvironmentVariable("PI_CODING_AGENT_DIR"),
                Path.Combine(home, ".pi", "agent"))!,
            TrajectorySource.ClaudeCode => Path.Combine(home, ".claude", "projects"),
            TrajectorySource.Codex => Path.Combine(home, ".codex", "sessions"),
            TrajectorySource.OpenClaw => FirstNonEmpty(
                Environment.GetEnvironmentVariable("OPENCLAW_STATE_DIR"),
                Environment.GetEnvironmentVariable("CLAWDBOT_STATE_DIR"),
                Directory.Exists(Path.Combine(home, ".openclaw"))
                    ? Path.Combine(home, ".openclaw")
                    : Path.Combine(home, ".clawdbot"))!,
            TrajectorySource.Hermes => Path.Combine(home, ".hermes"),
            TrajectorySource.Ahp => home,
            TrajectorySource.GrokBuild => ResolveGrokBuildRoot(home),
            TrajectorySource.Cursor => Expand(Environment.GetEnvironmentVariable("CURSOR_HOME")?.Trim() ?? Path.Combine(home, ".cursor")),
            _ => home,
        };
    }

    private static string ResolveGrokBuildRoot(string home)
    {
        var grokHome = Environment.GetEnvironmentVariable("GROK_HOME")?.Trim();
        if (!string.IsNullOrEmpty(grokHome))
        {
            return Path.Combine(Expand(grokHome), "sessions");
        }

        return Path.Combine(home, ".grok", "sessions");
    }

    public static string DescribeDefault(TrajectorySource source) => source switch
    {
        TrajectorySource.Pi => "~/.pi/agent (or PI_CODING_AGENT_DIR)",
        TrajectorySource.ClaudeCode => "~/.claude/projects",
        TrajectorySource.Codex => "~/.codex/sessions",
        TrajectorySource.OpenClaw => "~/.openclaw if present, else ~/.clawdbot (or OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR)",
        TrajectorySource.Hermes => "~/.hermes/state.db",
        TrajectorySource.Ahp => "explicit export root only (no home default)",
        TrajectorySource.GrokBuild => "$GROK_HOME/sessions or ~/.grok/sessions (or TRAJECTORY_GROK_BUILD_ROOT)",
        TrajectorySource.Cursor => "$CURSOR_HOME or ~/.cursor (or TRAJECTORY_CURSOR_ROOT)",
        _ => "n/a",
    };

    private static string Expand(string path) =>
        path.StartsWith("~/") || path == "~"
            ? Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                path == "~" ? string.Empty : path[2..])
            : path;

    private static string? FirstNonEmpty(params string?[] values) =>
        values.FirstOrDefault(static value => !string.IsNullOrWhiteSpace(value));
}

internal static class CliFormat
{
    public static string FormatBytes(long bytes)
    {
        string[] units = ["B", "KB", "MB", "GB"];
        double value = bytes;
        var unit = 0;
        while (value >= 1024 && unit < units.Length - 1)
        {
            value /= 1024;
            unit++;
        }

        return unit == 0 ? $"{bytes} B" : $"{value:0.#} {units[unit]}";
    }

    public static string Truncate(string value, int max) =>
        value.Length <= max ? value : value[..Math.Max(0, max - 1)] + "…";

    public static string FormatRelativeTime(DateTimeOffset? value, DateTimeOffset? now = null)
    {
        if (value is null)
        {
            return "—";
        }

        var clock = now ?? DateTimeOffset.UtcNow;
        var seconds = (long)(clock - value.Value).TotalSeconds;
        if (seconds < 5)
        {
            return "just now";
        }

        if (seconds < 60)
        {
            return $"{seconds}s ago";
        }

        var minutes = seconds / 60;
        if (minutes < 60)
        {
            return $"{minutes}m ago";
        }

        var hours = minutes / 60;
        if (hours < 24)
        {
            return $"{hours}h ago";
        }

        var days = hours / 24;
        return days < 7 ? $"{days}d ago" : value.Value.UtcDateTime.ToString("yyyy-MM-dd");
    }

    public static IReadOnlyList<TrajectoryListing> SortByActive(IEnumerable<TrajectoryListing> items) =>
        items
            .OrderByDescending(static item => item.UpdatedAt ?? DateTimeOffset.MinValue)
            .ThenBy(static item => item.Id, StringComparer.Ordinal)
            .ToList();

    public static string FormatSessionChoice(TrajectoryListing item, DateTimeOffset? now = null)
    {
        var rel = FormatRelativeTime(item.UpdatedAt, now);
        var title = string.IsNullOrWhiteSpace(item.Title) ? "—" : Truncate(item.Title.Trim(), 48);
        return $"{rel,-10}  {title}  {item.Id}";
    }
}

internal static class StreamCli
{
    public static readonly string[] FileSources =
    [
        "pi", "claude-code", "codex", "openclaw", "grok-build", "cursor",
    ];

    public static StreamDelivery ParseEmit(string? value)
    {
        var normalized = (value ?? "snapshot+delta").Trim().ToLowerInvariant()
            .Replace("_", "+", StringComparison.Ordinal)
            .Replace(" ", "", StringComparison.Ordinal);
        return normalized switch
        {
            "snapshot+delta" or "both" or "snapshotdelta" => StreamDelivery.Both,
            "snapshot" => StreamDelivery.Snapshot,
            "delta" => StreamDelivery.Delta,
            _ => throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                $"Unknown --emit '{value}'. Expected snapshot+delta, snapshot, or delta."),
        };
    }

    public static string EmitLabel(StreamDelivery delivery) => delivery switch
    {
        StreamDelivery.Both => "snapshot+delta",
        StreamDelivery.Snapshot => "snapshot",
        StreamDelivery.Delta => "delta",
        _ => delivery.ToString(),
    };

    public static string UtcStamp(DateTimeOffset? now = null) =>
        (now ?? DateTimeOffset.UtcNow).ToString("yyyy-MM-ddTHH:mm:ssZ");

    public static string CursorOffsetLabel(StreamPosition position) =>
        position is BytePosition bytePos
            ? $"byte next={bytePos.NextByteOffset} pending={bytePos.PendingByteLength}"
            : position.Kind;

    public static string RecordTimeLabel(IReadOnlyDictionary<string, object?> rec)
    {
        var raw = rec.TryGetValue("timestamp", out var ts) ? ts?.ToString()
            : rec.TryGetValue("source_timestamp", out var src) ? src?.ToString()
            : null;
        if (string.IsNullOrWhiteSpace(raw))
        {
            return "—";
        }

        return DateTimeOffset.TryParse(raw, out var parsed)
            ? parsed.ToUniversalTime().ToString("HH:mm:ssZ")
            : CliFormat.Truncate(raw, 20);
    }

    public static void PrintUpdate(StreamUpdate update, bool showContent, StreamDelivery emit, int index)
    {
        AnsiConsole.MarkupLine($"[bold]── {UtcStamp()}  stream update #{index}  (just now) ──[/]");
        AnsiConsole.MarkupLine($"[grey]kind[/]       {Markup.Escape(update.Kind)}");
        AnsiConsole.MarkupLine(
            $"[grey]revision[/]   {update.Revision.Revision} id={Markup.Escape(update.Revision.RevisionId)} gen={update.Revision.Generation}");
        AnsiConsole.MarkupLine(
            $"[grey]cursor[/]     source={Markup.Escape(update.Cursor.Source)} group={Markup.Escape(CliFormat.Truncate(update.Cursor.GroupId, 40))} gen={update.Cursor.Generation} pos={Markup.Escape(CursorOffsetLabel(update.Cursor.Position))}");
        if (update.Snapshot is not null)
        {
            AnsiConsole.MarkupLine(
                $"[grey]snapshot[/]   records={update.Snapshot.Records.Count} complete={update.Snapshot.Complete}");
        }
        else if (emit is StreamDelivery.Both or StreamDelivery.Snapshot)
        {
            AnsiConsole.MarkupLine("[grey]snapshot[/]   (omitted by delivery)");
        }

        if (update.Delta is not null)
        {
            var ops = update.Delta.Operations;
            var summary = string.Join(
                ", ",
                ops.GroupBy(static o => o.Op)
                    .OrderBy(static g => g.Key, StringComparer.Ordinal)
                    .Select(static g => $"{g.Key}={g.Count()}"));
            AnsiConsole.MarkupLine(
                $"[grey]delta[/]      ops={ops.Count}" + (summary.Length > 0 ? $" ({Markup.Escape(summary)})" : ""));
        }
        else if (emit is StreamDelivery.Both or StreamDelivery.Delta)
        {
            AnsiConsole.MarkupLine("[grey]delta[/]      (omitted by delivery)");
        }

        AnsiConsole.MarkupLine($"[grey]diagnostics[/] {update.Diagnostics.Count}");
        foreach (var diagnostic in update.Diagnostics.Take(8))
        {
            AnsiConsole.MarkupLine($"  [grey]{Markup.Escape(diagnostic.Code)}[/]  {Markup.Escape(diagnostic.Message)}");
        }

        if (update.Reset is not null)
        {
            AnsiConsole.MarkupLine($"[yellow]reset[/]      reason={Markup.Escape(update.Reset.Reason)}");
        }

        if (update.Error is { } err)
        {
            AnsiConsole.MarkupLine(
                $"[red]error[/]      {Markup.Escape(err.Code)}: {Markup.Escape(err.Message)}");
        }

        if (update.Snapshot is { Records.Count: > 0 })
        {
            var records = update.Snapshot.Records;
            var tail = records.TakeLast(20).ToList();
            var start = records.Count - tail.Count + 1;
            AnsiConsole.MarkupLine(
                $"[grey]latest {tail.Count} of {records.Count} records (live tail):[/]");
            if (showContent)
            {
                AnsiConsole.MarkupLine(
                    "[red bold]WARNING[/][red]: --show-content prints transcript-derived text. Treat as private.[/]");
            }

            var order = start;
            foreach (var streamRec in tail)
            {
                var rec = streamRec.Record;
                var role = rec.TryGetValue("role", out var roleVal) ? roleVal?.ToString() ?? "?" : "?";
                var kind = rec.TryGetValue("kind", out var kindVal)
                    ? kindVal?.ToString() ?? "?"
                    : rec.TryGetValue("type", out var typeVal) ? typeVal?.ToString() ?? "?" : "?";
                var when = RecordTimeLabel(rec);
                var snip = showContent
                    ? rec.TryGetValue("content", out var contentVal) && contentVal is string s
                        ? s
                        : JsonSerializer.Serialize(rec)
                    : "(content hidden)";
                AnsiConsole.MarkupLine(
                    $"  {order,3}  {Markup.Escape(when),-9}  {Markup.Escape(streamRec.Status),-12} {Markup.Escape(role),-10} {Markup.Escape(kind),-20} {Markup.Escape(CliFormat.Truncate(snip, 80))}");
                order++;
            }

            if (!showContent)
            {
                AnsiConsole.MarkupLine("[grey]Content omitted (privacy). Re-run with --show-content for snippets.[/]");
            }
        }
        else if (!showContent)
        {
            AnsiConsole.MarkupLine("[grey]Content omitted (privacy). Re-run with --show-content for snippets.[/]");
        }

        AnsiConsole.WriteLine();
    }

    public static async Task<int> FollowAsync(
        TrajectorySource source,
        string root,
        string path,
        string? groupId,
        StreamDelivery delivery,
        bool follow,
        double interval,
        int? maxUpdates,
        bool showContent)
    {
        AnsiConsole.MarkupLine("[bold deepskyblue1]Trajectory stream[/]  [grey]live file follow (not a daemon)[/]");
        AnsiConsole.MarkupLine($"[grey]source[/]   {Sources.WireName(source)}");
        AnsiConsole.MarkupLine($"[grey]root[/]     {Markup.Escape(root)}");
        AnsiConsole.MarkupLine($"[grey]path[/]     {Markup.Escape(path)}");
        AnsiConsole.MarkupLine($"[grey]emit[/]     {EmitLabel(delivery)} (delivery={delivery.ToString().ToLowerInvariant()})");
        AnsiConsole.MarkupLine($"[grey]follow[/]   {follow}");
        if (!showContent)
        {
            AnsiConsole.MarkupLine("[grey]Privacy: content hidden unless --show-content.[/]");
        }

        if (follow)
        {
            AnsiConsole.WriteLine();
            AnsiConsole.MarkupLine(
                "[bold]Following live.[/] Showing the latest records (tail), not the start of the session.");
            if (source == TrajectorySource.GrokBuild)
            {
                AnsiConsole.MarkupLine(
                    "[grey]Grok chat_history.jsonl grows when a conversation item is committed (not token-by-token updates.jsonl).[/]");
            }

            AnsiConsole.MarkupLine(
                "[grey]Ctrl-C stops. A watching line ticks while waiting for new complete lines.[/]");
        }

        AnsiConsole.WriteLine();

        // Grok history and Cursor transcript lines do not embed the session id; listing id is the group.
        // Other file sources lock group from the transcript.
        if (source is not (TrajectorySource.GrokBuild or TrajectorySource.Cursor))
        {
            groupId = null;
        }

        using var stream = FileTrajectoryStream.Open(new FileTrajectoryStreamOptions
        {
            Root = root,
            Path = path,
            Source = source,
            GroupId = groupId,
            Stream = new StreamOptions
            {
                Source = source,
                GroupId = groupId,
                Delivery = delivery,
            },
            PollInterval = TimeSpan.FromSeconds(Math.Max(0, interval)),
        });

        var seen = 0;
        var lastBeat = DateTime.UtcNow - TimeSpan.FromSeconds(2);
        var tty = !Console.IsOutputRedirected;
        while (true)
        {
            var update = stream.Poll();
            if (update is not null && update.Kind != "unchanged")
            {
                if (follow && tty)
                {
                    Console.WriteLine();
                }

                seen++;
                PrintUpdate(update, showContent, delivery, seen);
                if (maxUpdates is int max && seen >= max)
                {
                    break;
                }
            }

            if (!follow)
            {
                break;
            }

            if (maxUpdates is int max2 && seen >= max2)
            {
                break;
            }

            if (DateTime.UtcNow - lastBeat >= TimeSpan.FromSeconds(1))
            {
                lastBeat = DateTime.UtcNow;
                var sizeLabel = File.Exists(path) ? $"{new FileInfo(path).Length} B" : "—";
                var seenLabel = seen == 0 ? "—" : seen.ToString();
                var line =
                    $"watching  {UtcStamp()}  file={sizeLabel}  last update #{seenLabel}  waiting for new complete lines";
                if (tty)
                {
                    Console.Write($"\r{line,-100}");
                }
                else
                {
                    AnsiConsole.MarkupLine($"[grey]{Markup.Escape(line)}[/]");
                }
            }

            if (interval > 0)
            {
                await Task.Delay(TimeSpan.FromSeconds(interval)).ConfigureAwait(false);
            }
        }

        if (follow && tty)
        {
            Console.WriteLine();
        }

        if (seen == 0)
        {
            AnsiConsole.MarkupLine("[grey]No stream updates (empty or unchanged prefix).[/]");
        }
        else
        {
            AnsiConsole.MarkupLine(
                $"[grey]Emitted {seen} update(s). Process exit ends follow (not a daemon).[/]");
        }

        return 0;
    }
}

internal class GlobalSettings : CommandSettings
{
    [CommandOption("-s|--source <SOURCE>")]
    [Description("Transcript source: pi, claude-code, codex, openclaw, hermes, ahp, grok-build, cursor (aliases: grok, cursor-agent).")]
    public string? Source { get; init; }

    [CommandOption("-r|--root <PATH>")]
    [Description("Override the local store root for the selected source.")]
    public string? Root { get; init; }

    [CommandOption("--limit <N>")]
    [Description("Maximum sessions to list (1-1000). Default 50.")]
    [DefaultValue(50)]
    public int Limit { get; init; } = 50;

    [CommandOption("--show-content")]
    [Description("Include record content snippets. WARNING: may contain private data.")]
    [DefaultValue(false)]
    public bool ShowContent { get; init; }
}

internal sealed class ListCommand : AsyncCommand<ListCommand.Settings>
{
    public sealed class Settings : GlobalSettings;

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        var source = Sources.Parse(settings.Source ?? PromptSource());
        var root = StoreRoots.Resolve(source, settings.Root);
        AnsiConsole.MarkupLine($"[grey]Source[/] {Sources.WireName(source)}  [grey]root[/] {Markup.Escape(root)}");

        var page = await TrajectoryConverter.ListTrajectoriesAsync(
            source,
            root,
            limit: settings.Limit);

        if (page.Items.Count == 0)
        {
            AnsiConsole.MarkupLine("[yellow]No sessions found.[/] Empty or missing store is not an error.");
            if (source == TrajectorySource.Hermes)
            {
                AnsiConsole.MarkupLine(
                    "[grey]Hermes core listing is SQLite-free and returns empty pages. Export message JSON and use `show --path`.[/]");
            }

            return 0;
        }

        var table = new Table().Border(TableBorder.Rounded);
        table.AddColumn("Active");
        table.AddColumn("Id");
        table.AddColumn("Title");
        table.AddColumn("Size");
        table.AddColumn("Path");

        foreach (var item in CliFormat.SortByActive(page.Items))
        {
            table.AddRow(
                Markup.Escape(CliFormat.FormatRelativeTime(item.UpdatedAt)),
                Markup.Escape(item.Id),
                Markup.Escape(string.IsNullOrWhiteSpace(item.Title) ? "—" : item.Title),
                item.SizeBytes is null ? "—" : CliFormat.FormatBytes(item.SizeBytes.Value),
                Markup.Escape(item.Path));
        }

        AnsiConsole.Write(table);
        if (page.NextCursor is not null)
        {
            AnsiConsole.MarkupLine($"[grey]More sessions available (cursor pagination). Showing first {page.Items.Count}.[/]");
        }

        return 0;
    }

    private static string PromptSource() =>
        AnsiConsole.Prompt(
            new SelectionPrompt<string>()
                .Title("Select a source")
                .AddChoices(Sources.Names));
}

internal sealed class ShowCommand : AsyncCommand<ShowCommand.Settings>
{
    public sealed class Settings : GlobalSettings
    {
        [CommandOption("-p|--path <PATH>")]
        [Description("Path to a session transcript file (JSONL or Hermes JSON export).")]
        public string? Path { get; init; }

        [CommandOption("--id <ID>")]
        [Description("Session id from listing; resolved under the store root when --path is omitted.")]
        public string? Id { get; init; }

        [CommandOption("--format <FORMAT>")]
        [Description("Summary projection: messages, hypabolic, or both (default).")]
        [DefaultValue("both")]
        public string Format { get; init; } = "both";
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        var source = Sources.Parse(settings.Source ?? "pi");
        var root = StoreRoots.Resolve(source, settings.Root);
        var resolved = await ResolvePathAsync(source, root, settings.Path, settings.Id, settings.Limit);
        if (resolved is null)
        {
            return 2;
        }

        return SessionSummary.Print(
            source,
            resolved.Value.Path,
            settings.ShowContent,
            settings.Format,
            groupId: resolved.Value.GroupId);
    }

    private static async Task<(string Path, string? GroupId)?> ResolvePathAsync(
        TrajectorySource source,
        string root,
        string? path,
        string? id,
        int limit)
    {
        if (!string.IsNullOrWhiteSpace(path))
        {
            return (path, string.IsNullOrWhiteSpace(id) ? null : id);
        }

        if (string.IsNullOrWhiteSpace(id))
        {
            AnsiConsole.MarkupLine("[red]Provide --path or --id.[/]");
            return null;
        }

        var page = await TrajectoryConverter.ListTrajectoriesAsync(source, root, limit: limit);
        var match = page.Items.FirstOrDefault(item =>
            string.Equals(item.Id, id, StringComparison.Ordinal));
        if (match is null)
        {
            AnsiConsole.MarkupLine($"[red]Session id '{Markup.Escape(id)}' not found under {Markup.Escape(root)}.[/]");
            return null;
        }

        return (match.Path, match.Id);
    }
}

internal sealed class InteractiveCommand : AsyncCommand<InteractiveCommand.Settings>
{
    public sealed class Settings : GlobalSettings
    {
        [CommandOption("--id <ID>")]
        [Description("Session id from listing; skip the picker.")]
        public string? Id { get; init; }

        [CommandOption("--watch")]
        [Description("After selecting a session, follow it live (skip the action prompt).")]
        [DefaultValue(false)]
        public bool Watch { get; init; }

        [CommandOption("--emit <MODE>")]
        [Description("Watch delivery: snapshot+delta (default), snapshot, or delta.")]
        [DefaultValue("snapshot+delta")]
        public string Emit { get; init; } = "snapshot+delta";

        [CommandOption("--interval <SECONDS>")]
        [Description("Poll interval seconds when watching (default 0.05).")]
        [DefaultValue(0.05)]
        public double Interval { get; init; } = 0.05;

        [CommandOption("--max-updates <N>")]
        [Description("Stop after N stream updates (tests/demos).")]
        public int? MaxUpdates { get; init; }
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        AnsiConsole.Write(new FigletText("Trajectory").Color(Color.DeepSkyBlue1));
        AnsiConsole.MarkupLine("[grey]Local sample TUI — Privacy: content is hidden unless --show-content.[/]");
        AnsiConsole.WriteLine();

        var sourceName = settings.Source
            ?? AnsiConsole.Prompt(
                new SelectionPrompt<string>()
                    .Title("Which agent source should we browse?")
                    .PageSize(10)
                    .AddChoices(Sources.Names));
        var source = Sources.Parse(sourceName);
        if (settings.Watch && !StreamCli.FileSources.Contains(Sources.WireName(source), StringComparer.Ordinal))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                $"Watch live supports file JSONL sources only: {string.Join(", ", StreamCli.FileSources)}. Use show for Hermes exports; ahp-stream for AHP.");
        }

        var root = StoreRoots.Resolve(source, settings.Root);

        AnsiConsole.MarkupLine(
            $"[grey]Default for {Sources.WireName(source)}:[/] {Markup.Escape(StoreRoots.DescribeDefault(source))}");
        AnsiConsole.MarkupLine($"[grey]Using root[/] {Markup.Escape(root)}");
        AnsiConsole.WriteLine();

        TrajectoryListingPage page;
        try
        {
            page = await TrajectoryConverter.ListTrajectoriesAsync(
                source,
                root,
                limit: settings.Limit);
        }
        catch (TrajectoryNormalizationException error)
        {
            AnsiConsole.MarkupLine($"[red]{Markup.Escape(error.Code.ToString())}:[/] {Markup.Escape(error.Message)}");
            return 2;
        }

        if (page.Items.Count == 0)
        {
            AnsiConsole.MarkupLine("[yellow]No sessions discovered in this store.[/]");
            if (source == TrajectorySource.Hermes)
            {
                AnsiConsole.MarkupLine(
                    "[grey]Hermes listing stays SQLite-free in the core package. Export a session as JSON and run:[/]");
                AnsiConsole.MarkupLine(
                    "[grey]  trajectory show --source hermes --path ./session.json[/]");
            }
            else
            {
                AnsiConsole.MarkupLine("[grey]Create a session with the agent, or pass --root to another store.[/]");
            }

            if (settings.Watch || !string.IsNullOrWhiteSpace(settings.Id))
            {
                return 0;
            }

            if (AnsiConsole.Confirm("Normalize a transcript file by path instead?", defaultValue: false))
            {
                var filePath = AnsiConsole.Ask<string>("Path to transcript:");
                return SessionSummary.Print(source, filePath, settings.ShowContent, "both");
            }

            return 0;
        }

        TrajectoryListing listing;
        if (!string.IsNullOrWhiteSpace(settings.Id))
        {
            var match = page.Items.FirstOrDefault(item =>
                string.Equals(item.Id, settings.Id, StringComparison.Ordinal));
            if (match is null)
            {
                throw new TrajectoryNormalizationException(
                    NormalizationErrorCode.InvalidInput,
                    $"Session id '{settings.Id}' not found under {root}.");
            }

            listing = match;
        }
        else
        {
            var ordered = CliFormat.SortByActive(page.Items);
            var choices = ordered
                .Select(static item => CliFormat.FormatSessionChoice(item))
                .ToList();
            choices.Add("(quit)");

            var selected = AnsiConsole.Prompt(
                new SelectionPrompt<string>()
                    .Title($"Select a session ({ordered.Count} shown, most recently active first)")
                    .PageSize(Math.Min(20, choices.Count))
                    .MoreChoicesText("[grey](move up/down)[/]")
                    .AddChoices(choices));

            if (selected == "(quit)")
            {
                return 0;
            }

            listing = ordered[choices.IndexOf(selected)];
        }

        AnsiConsole.WriteLine();
        return await ViewSelectedSessionAsync(source, root, listing, settings).ConfigureAwait(false);
    }

    private static async Task<int> ViewSelectedSessionAsync(
        TrajectorySource source,
        string root,
        TrajectoryListing listing,
        Settings settings)
    {
        var canWatch = StreamCli.FileSources.Contains(Sources.WireName(source), StringComparer.Ordinal);
        if (settings.Watch)
        {
            if (!canWatch)
            {
                throw new TrajectoryNormalizationException(
                    NormalizationErrorCode.InvalidInput,
                    $"Watch live supports file JSONL sources only: {string.Join(", ", StreamCli.FileSources)}. Use show for Hermes exports; ahp-stream for AHP.");
            }

            return await StreamCli.FollowAsync(
                source,
                root,
                listing.Path,
                listing.Id,
                StreamCli.ParseEmit(settings.Emit),
                follow: true,
                settings.Interval,
                settings.MaxUpdates,
                settings.ShowContent).ConfigureAwait(false);
        }

        if (!string.IsNullOrWhiteSpace(settings.Id) && Console.IsInputRedirected)
        {
            return SessionSummary.Print(
                source,
                listing.Path,
                settings.ShowContent,
                "both",
                groupId: listing.Id);
        }

        var actions = canWatch
            ? new[] { "Watch live (follow as it grows)", "Show snapshot (one-shot normalize)", "(quit)" }
            : new[] { "Show snapshot (one-shot normalize)", "(quit)" };
        var action = AnsiConsole.Prompt(
            new SelectionPrompt<string>()
                .Title("How do you want to view this session?")
                .AddChoices(actions));
        if (action == "(quit)")
        {
            return 0;
        }

        if (action.StartsWith("Watch", StringComparison.Ordinal))
        {
            return await StreamCli.FollowAsync(
                source,
                root,
                listing.Path,
                listing.Id,
                StreamCli.ParseEmit(settings.Emit),
                follow: true,
                settings.Interval,
                settings.MaxUpdates,
                settings.ShowContent).ConfigureAwait(false);
        }

        return SessionSummary.Print(
            source,
            listing.Path,
            settings.ShowContent,
            "both",
            groupId: listing.Id);
    }

}

internal static class SessionSummary
{
    public static int Print(
        TrajectorySource source,
        string path,
        bool showContent,
        string format,
        string? groupId = null)
    {
        if (!File.Exists(path))
        {
            AnsiConsole.MarkupLine($"[red]File not found:[/] {Markup.Escape(path)}");
            return 2;
        }

        string transcript;
        try
        {
            transcript = File.ReadAllText(path);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            AnsiConsole.MarkupLine($"[red]Could not read transcript:[/] {Markup.Escape(error.Message)}");
            return 2;
        }

        TrajectoryIR ir;
        try
        {
            SourceContext? context = string.IsNullOrEmpty(groupId)
                ? null
                : new SourceContext { GroupId = groupId };
            ir = TrajectoryConverter.NormalizeToIR(source, transcript, sourceContext: context);
        }
        catch (TrajectoryNormalizationException error)
        {
            AnsiConsole.MarkupLine($"[red]{Markup.Escape(error.Code.ToString())}:[/] {Markup.Escape(error.Message)}");
            return 2;
        }

        AnsiConsole.Write(new Rule($"[bold]{Sources.WireName(source)}[/] {Markup.Escape(Path.GetFileName(path))}").LeftJustified());
        AnsiConsole.MarkupLine($"[grey]path[/]     {Markup.Escape(path)}");
        AnsiConsole.MarkupLine($"[grey]group[/]    {Markup.Escape(ir.GroupId)}");
        AnsiConsole.MarkupLine($"[grey]source[/]   {Markup.Escape(ir.SourceName)}");
        AnsiConsole.MarkupLine($"[grey]records[/]  {ir.Records.Count}");
        AnsiConsole.MarkupLine($"[grey]partial[/]  {ir.Config.SourceContext.Partial}");

        var roleCounts = ir.Records
            .GroupBy(static record => record.Role.ToString().ToLowerInvariant())
            .OrderBy(static group => group.Key, StringComparer.Ordinal)
            .ToDictionary(static group => group.Key, static group => group.Count(), StringComparer.Ordinal);

        var toolNames = ir.Records
            .OfType<AssistantToolCallsIR>()
            .SelectMany(static record => record.ToolCalls)
            .Select(static call => call.Name)
            .ToArray();
        var toolCallCount = toolNames.Length;
        var uniqueTools = toolNames
            .Distinct(StringComparer.Ordinal)
            .OrderBy(static name => name, StringComparer.Ordinal)
            .ToArray();

        var grid = new Grid().AddColumn().AddColumn();
        grid.AddRow("[bold]Roles[/]", string.Join(", ", roleCounts.Select(pair => $"{pair.Key}={pair.Value}")));
        grid.AddRow("[bold]Tool calls[/]", $"{toolCallCount} total, {uniqueTools.Length} unique");
        if (uniqueTools.Length > 0)
        {
            var tools = string.Join(", ", uniqueTools.Take(12)) + (uniqueTools.Length > 12 ? "…" : string.Empty);
            grid.AddRow("[bold]Tools[/]", Markup.Escape(tools));
        }

        grid.AddRow("[bold]Diagnostics[/]", ir.Diagnostics.Count.ToString());
        AnsiConsole.Write(grid);

        if (ir.Diagnostics.Count > 0)
        {
            var diagTable = new Table().Border(TableBorder.Simple).HideHeaders();
            diagTable.AddColumn("code");
            diagTable.AddColumn("message");
            foreach (var diagnostic in ir.Diagnostics.Take(12))
            {
                diagTable.AddRow(
                    Markup.Escape(diagnostic.Code),
                    Markup.Escape(diagnostic.Message));
            }

            AnsiConsole.Write(diagTable);
            if (ir.Diagnostics.Count > 12)
            {
                AnsiConsole.MarkupLine($"[grey]…and {ir.Diagnostics.Count - 12} more diagnostics[/]");
            }
        }

        var formats = format.Trim().ToLowerInvariant();
        if (formats is "both" or "hypabolic" or "all")
        {
            try
            {
                var hypabolic = TrajectoryConverter.NormalizeToHypabolic(source, transcript);
                AnsiConsole.WriteLine();
                AnsiConsole.MarkupLine(
                    $"[bold]Hypabolic[/] trajectoryId={Markup.Escape(hypabolic.TrajectoryId)} schema={hypabolic.SchemaId} v{hypabolic.SchemaVersion} records={hypabolic.Records.Count}");
            }
            catch (TrajectoryNormalizationException error)
            {
                AnsiConsole.MarkupLine($"[yellow]Hypabolic projection skipped:[/] {Markup.Escape(error.Message)}");
            }
        }

        if (formats is "both" or "messages" or "letta" or "all")
        {
            try
            {
                var messages = TrajectoryConverter.NormalizeTranscript(source, transcript);
                AnsiConsole.MarkupLine(
                    $"[bold]Messages[/] records={messages.Records.Count} diagnostics={messages.Diagnostics.Count}");
            }
            catch (TrajectoryNormalizationException error)
            {
                AnsiConsole.MarkupLine($"[yellow]Message trajectory skipped:[/] {Markup.Escape(error.Message)}");
            }
        }

        if (showContent)
        {
            AnsiConsole.WriteLine();
            AnsiConsole.MarkupLine("[red bold]WARNING[/][red]: --show-content prints transcript-derived text. Treat as private.[/]");
            var contentTable = new Table().Border(TableBorder.Rounded);
            contentTable.AddColumn("#");
            contentTable.AddColumn("Role");
            contentTable.AddColumn("Kind");
            contentTable.AddColumn("Snippet");
            var order = 0;
            foreach (var record in ir.Records)
            {
                order++;
                var snippet = record switch
                {
                    MessageIR message => CliFormat.Truncate(message.Content, 120),
                    AssistantToolCallsIR calls => string.Join(
                        ", ",
                        calls.ToolCalls.Select(call => $"{call.Name}({CliFormat.Truncate(call.ArgumentsJson, 40)})")),
                    ToolResultIR result => CliFormat.Truncate(result.Content, 120),
                    MetaIR meta => $"source={meta.SourceName} model={meta.Model ?? "—"}",
                    _ => "—",
                };
                contentTable.AddRow(
                    order.ToString(),
                    record.Role.ToString().ToLowerInvariant(),
                    record.Kind.ToString(),
                    Markup.Escape(snippet));
                if (order >= 40)
                {
                    break;
                }
            }

            AnsiConsole.Write(contentTable);
            if (ir.Records.Count > 40)
            {
                AnsiConsole.MarkupLine($"[grey]Showing first 40 of {ir.Records.Count} records.[/]");
            }
        }
        else
        {
            AnsiConsole.MarkupLine("[grey]Content omitted (privacy). Re-run with --show-content to include snippets.[/]");
        }

        return 0;
    }
}

internal sealed class StreamCommand : AsyncCommand<StreamCommand.Settings>
{
    public sealed class Settings : GlobalSettings
    {
        [CommandOption("-p|--path <PATH>")]
        [Description("Path to a JSONL session file to follow.")]
        public string? Path { get; init; }

        [CommandOption("--id <ID>")]
        [Description("Session id from listing; requires --root.")]
        public string? Id { get; init; }

        [CommandOption("--emit <MODE>")]
        [Description("Delivery: snapshot+delta (default), snapshot, or delta.")]
        [DefaultValue("snapshot+delta")]
        public string Emit { get; init; } = "snapshot+delta";

        [CommandOption("--follow")]
        [Description("Keep polling until Ctrl-C or --max-updates (not a daemon).")]
        [DefaultValue(false)]
        public bool Follow { get; init; }

        [CommandOption("--interval <SECONDS>")]
        [Description("Poll interval seconds when --follow (default 0.05).")]
        [DefaultValue(0.05)]
        public double Interval { get; init; } = 0.05;

        [CommandOption("--max-updates <N>")]
        [Description("Stop after N stream updates (tests/demos).")]
        public int? MaxUpdates { get; init; }
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        var source = Sources.Parse(settings.Source ?? "pi");
        var wire = Sources.WireName(source);
        if (!StreamCli.FileSources.Contains(wire, StringComparer.Ordinal))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                $"stream supports file JSONL sources only: {string.Join(", ", StreamCli.FileSources)}. Use ahp-stream for AHP.");
        }

        var delivery = StreamCli.ParseEmit(settings.Emit);
        string root;
        string path;
        string? groupId;
        var follow = settings.Follow;
        if (string.IsNullOrWhiteSpace(settings.Path) && string.IsNullOrWhiteSpace(settings.Id))
        {
            var picked = await PickListedSessionAsync(source, settings).ConfigureAwait(false);
            if (picked is null)
            {
                return 0;
            }

            (root, path, groupId) = picked.Value;
            follow = true;
        }
        else
        {
            (root, path, groupId) = await ResolveStreamTargetAsync(source, settings).ConfigureAwait(false);
        }

        return await StreamCli.FollowAsync(
            source,
            root,
            path,
            groupId,
            delivery,
            follow,
            settings.Interval,
            settings.MaxUpdates,
            settings.ShowContent).ConfigureAwait(false);
    }

    private static async Task<(string Root, string Path, string? GroupId)?> PickListedSessionAsync(
        TrajectorySource source,
        Settings settings)
    {
        if (Console.IsInputRedirected)
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "stream without --path/--id needs a TTY to pick a session, or pass --path / --id --root. Use browse --watch to pick from the store UI.");
        }

        var root = StoreRoots.Resolve(source, settings.Root);
        var page = await TrajectoryConverter.ListTrajectoriesAsync(source, root, limit: settings.Limit)
            .ConfigureAwait(false);
        if (page.Items.Count == 0)
        {
            AnsiConsole.MarkupLine("[yellow]No sessions found.[/] Empty or missing store is not an error.");
            return null;
        }

        var ordered = CliFormat.SortByActive(page.Items);
        var choices = ordered
            .Select(static item => CliFormat.FormatSessionChoice(item))
            .ToList();
        choices.Add("(quit)");
        var selected = AnsiConsole.Prompt(
            new SelectionPrompt<string>()
                .Title($"Select a session to follow ({ordered.Count} shown, most recently active first)")
                .PageSize(Math.Min(20, choices.Count))
                .AddChoices(choices));
        if (selected == "(quit)")
        {
            return null;
        }

        var listing = ordered[choices.IndexOf(selected)];
        return (root, listing.Path, listing.Id);
    }

    private static async Task<(string Root, string Path, string? GroupId)> ResolveStreamTargetAsync(
        TrajectorySource source,
        Settings settings)
    {
        if (!string.IsNullOrWhiteSpace(settings.Path))
        {
            var path = settings.Path.Trim();
            var root = !string.IsNullOrWhiteSpace(settings.Root)
                ? settings.Root.Trim()
                : System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(path))
                  ?? throw new TrajectoryNormalizationException(
                      NormalizationErrorCode.InvalidInput,
                      "Could not derive root from path.");
            return (root, path, string.IsNullOrWhiteSpace(settings.Id) ? null : settings.Id);
        }

        if (string.IsNullOrWhiteSpace(settings.Id))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "stream requires --path or --id (with --root for listing resolution).");
        }

        if (string.IsNullOrWhiteSpace(settings.Root))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "stream --id requires an explicit --root (no implicit home multi-session watch).");
        }

        var page = await TrajectoryConverter.ListTrajectoriesAsync(source, settings.Root, limit: settings.Limit)
            .ConfigureAwait(false);
        var match = page.Items.FirstOrDefault(item =>
            string.Equals(item.Id, settings.Id, StringComparison.Ordinal));
        if (match is null)
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                $"Session id '{settings.Id}' not found under {settings.Root}.");
        }

        return (settings.Root, match.Path, settings.Id);
    }
}

internal sealed class AhpStreamCommand : AsyncCommand<AhpStreamCommand.Settings>
{
    public sealed class Settings : GlobalSettings
    {
        [CommandOption("--url <URL>")]
        [Description("Host URL. Sample supports fake:// (in-memory FakeAhpHost) only.")]
        public string? Url { get; init; }

        [CommandOption("--chat <URI>")]
        [Description("AHP chat channel URI (ahp-chat:/…).")]
        public string? Chat { get; init; }

        [CommandOption("--from-seq <N>")]
        [Description("Optional subscribe fromSeq.")]
        public long? FromSeq { get; init; }

        [CommandOption("--token <TOKEN>")]
        [Description("Auth token for callback (never stored on stream state).")]
        public string? Token { get; init; }

        [CommandOption("--snapshot-path <PATH>")]
        [Description("fake://: Shape A snapshot JSON for FakeAhpHost.")]
        public string? SnapshotPath { get; init; }

        [CommandOption("--actions-path <PATH>")]
        [Description("fake://: ActionEnvelope JSONL for FakeAhpHost.")]
        public string? ActionsPath { get; init; }

        [CommandOption("--emit <MODE>")]
        [Description("Delivery: snapshot+delta (default), snapshot, or delta.")]
        [DefaultValue("snapshot+delta")]
        public string Emit { get; init; } = "snapshot+delta";

        [CommandOption("--max-updates <N>")]
        [Description("Stop after N stream updates (tests/demos).")]
        public int? MaxUpdates { get; init; }
    }

    public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
    {
        if (string.IsNullOrWhiteSpace(settings.Chat))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "ahp-stream requires --chat <ahp-chat:/…>.");
        }

        var chat = settings.Chat.Trim();
        var url = string.IsNullOrWhiteSpace(settings.Url) ? "fake://demo" : settings.Url.Trim();
        var delivery = StreamCli.ParseEmit(settings.Emit);

        AnsiConsole.MarkupLine("[bold deepskyblue1]Trajectory ahp-stream[/]  [grey]sample client demo (not a daemon)[/]");
        AnsiConsole.MarkupLine($"[grey]url[/]      {Markup.Escape(url)}");
        AnsiConsole.MarkupLine($"[grey]chat[/]     {Markup.Escape(chat)}");
        AnsiConsole.MarkupLine($"[grey]emit[/]     {StreamCli.EmitLabel(delivery)}");
        if (settings.FromSeq is not null)
        {
            AnsiConsole.MarkupLine($"[grey]from-seq[/] {settings.FromSeq}");
        }

        if (!settings.ShowContent)
        {
            AnsiConsole.MarkupLine("[grey]Privacy: content hidden unless --show-content.[/]");
        }

        AnsiConsole.WriteLine();

        var scheme = url.Contains(':', StringComparison.Ordinal)
            ? url.Split(':', 2)[0].ToLowerInvariant()
            : url.ToLowerInvariant();
        if (scheme is not ("fake" or "memory" or "test"))
        {
            throw new TrajectoryNormalizationException(
                NormalizationErrorCode.InvalidInput,
                "Sample ahp-stream supports url scheme fake:// (in-memory FakeAhpHost) only. " +
                "Wire AhpStreamClient with your WebSocket AhpTransport for live hosts " +
                "(see docs/ahp-client.md). Example: --url fake://demo");
        }

        var pair = new InMemoryAhpTransportPair();
        JsonObject? snapshot = null;
        var actions = new List<JsonObject>();
        if (!string.IsNullOrWhiteSpace(settings.SnapshotPath))
        {
            snapshot = JsonNode.Parse(await File.ReadAllTextAsync(settings.SnapshotPath).ConfigureAwait(false)) as JsonObject;
        }

        if (!string.IsNullOrWhiteSpace(settings.ActionsPath))
        {
            foreach (var line in await File.ReadAllLinesAsync(settings.ActionsPath).ConfigureAwait(false))
            {
                if (string.IsNullOrWhiteSpace(line))
                {
                    continue;
                }

                if (JsonNode.Parse(line) is JsonObject env)
                {
                    actions.Add(env);
                }
            }
        }

        if (snapshot is null && actions.Count == 0)
        {
            snapshot = new JsonObject
            {
                ["ahpProtocolVersion"] = "0.7.0",
                ["chat"] = new JsonObject
                {
                    ["id"] = chat,
                    ["turns"] = new JsonArray(),
                    ["activeTurn"] = null,
                },
            };
        }

        var token = settings.Token ?? Environment.GetEnvironmentVariable("TRAJECTORY_AHP_TOKEN");
        var host = new FakeAhpHost(
            pair.Host,
            new FakeAhpHostScript
            {
                InitialSnapshot = snapshot,
                InitialActions = actions,
                RequireAuth = !string.IsNullOrEmpty(token),
                AcceptToken = token ?? "test-token",
            },
            chat);

        var updatesSeen = 0;
        var ready = false;
        var client = new AhpStreamClient(
            pair.Client,
            new AhpClientOptions
            {
                ChatChannel = chat,
                Auth = token is null ? null : _ => new AhpAuthCredentials(token),
                StreamOptions = new StreamOptions
                {
                    Source = TrajectorySource.Ahp,
                    GroupId = chat,
                    Delivery = delivery,
                },
                FromServerSeq = settings.FromSeq,
            },
            ev =>
            {
                if (ev.Kind == AhpClientEventKind.StreamUpdate && ev.Update is not null)
                {
                    updatesSeen++;
                    StreamCli.PrintUpdate(ev.Update, settings.ShowContent, delivery, updatesSeen);
                }
                else if (ev.Kind == AhpClientEventKind.Ready)
                {
                    ready = true;
                    AnsiConsole.MarkupLine("[grey]AHP client ready (subscribe complete).[/]");
                }
                else if (ev.Kind is AhpClientEventKind.AuthRequired or AhpClientEventKind.AuthFailed
                         or AhpClientEventKind.ResyncRequired or AhpClientEventKind.Backpressure
                         or AhpClientEventKind.Error or AhpClientEventKind.Disconnected)
                {
                    AnsiConsole.MarkupLine(
                        $"[yellow]{Markup.Escape(ev.Code ?? ev.Kind.ToString())}[/]  {Markup.Escape(ev.Message ?? ev.Kind.ToString())}");
                }
            });

        try
        {
            client.Start();
            if (settings.MaxUpdates is int max)
            {
                var deadline = DateTime.UtcNow.AddSeconds(2);
                while (updatesSeen < max && DateTime.UtcNow < deadline)
                {
                    await Task.Delay(10).ConfigureAwait(false);
                }
            }
            else
            {
                await Task.Delay(50).ConfigureAwait(false);
            }

            if (updatesSeen == 0 && !ready)
            {
                AnsiConsole.MarkupLine("[yellow]No AHP ready/update events. Check --url / --chat / fixtures.[/]");
            }
            else
            {
                AnsiConsole.MarkupLine(
                    $"[grey]Emitted {updatesSeen} stream update(s). Cancel leaves last cursor valid; not a daemon.[/]");
            }

            return 0;
        }
        finally
        {
            client.Cancel();
            host.Close();
        }
    }
}
