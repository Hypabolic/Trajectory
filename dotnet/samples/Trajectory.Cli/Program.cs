using System.ComponentModel;
using Hypabolic.Trajectory;
using Hypabolic.Trajectory.Listing;
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
            config.SetApplicationName("trajectory");
            config.ValidateExamples();
            config.AddCommand<ListCommand>("list")
                .WithDescription("List discovered local sessions for a source.");
            config.AddCommand<ShowCommand>("show")
                .WithDescription("Normalize a session file and print a privacy-safe summary.");
            config.AddCommand<InteractiveCommand>("browse")
                .WithDescription("Interactive session browser (default when no command is given).");
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
    ];

    public static TrajectorySource Parse(string value) => value.Trim().ToLowerInvariant() switch
    {
        "pi" => TrajectorySource.Pi,
        "claude-code" or "claudecode" or "claude" => TrajectorySource.ClaudeCode,
        "codex" => TrajectorySource.Codex,
        "openclaw" => TrajectorySource.OpenClaw,
        "hermes" => TrajectorySource.Hermes,
        "ahp" => TrajectorySource.Ahp,
        _ => throw new TrajectoryNormalizationException(
            NormalizationErrorCode.UnknownSource,
            $"Unknown source '{value}'. Expected one of: {string.Join(", ", Names)}."),
    };

    public static string WireName(TrajectorySource source) => source switch
    {
        TrajectorySource.Pi => "pi",
        TrajectorySource.ClaudeCode => "claude-code",
        TrajectorySource.Codex => "codex",
        TrajectorySource.OpenClaw => "openclaw",
        TrajectorySource.Hermes => "hermes",
        TrajectorySource.Ahp => "ahp",
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
            _ => home,
        };
    }

    public static string DescribeDefault(TrajectorySource source) => source switch
    {
        TrajectorySource.Pi => "~/.pi/agent (or PI_CODING_AGENT_DIR)",
        TrajectorySource.ClaudeCode => "~/.claude/projects",
        TrajectorySource.Codex => "~/.codex/sessions",
        TrajectorySource.OpenClaw => "~/.openclaw if present, else ~/.clawdbot (or OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR)",
        TrajectorySource.Hermes => "~/.hermes/state.db",
        TrajectorySource.Ahp => "explicit export root only (no home default)",
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
}

internal class GlobalSettings : CommandSettings
{
    [CommandOption("-s|--source <SOURCE>")]
    [Description("Transcript source: pi, claude-code, codex, openclaw, hermes, ahp.")]
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

            if (source == TrajectorySource.Ahp)
            {
                AnsiConsole.MarkupLine(
                    "[grey]AHP listing is Phase 3; use `show --path` with a Shape A snapshot export.[/]");
            }

            return 0;
        }

        var table = new Table().Border(TableBorder.Rounded);
        table.AddColumn("Id");
        table.AddColumn("Updated (UTC)");
        table.AddColumn("Size");
        table.AddColumn("Title");
        table.AddColumn("Path");

        foreach (var item in page.Items)
        {
            table.AddRow(
                Markup.Escape(item.Id),
                item.UpdatedAt?.ToString("u") ?? "—",
                item.SizeBytes is null ? "—" : CliFormat.FormatBytes(item.SizeBytes.Value),
                Markup.Escape(item.Title ?? "—"),
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
        var path = await ResolvePathAsync(source, root, settings.Path, settings.Id, settings.Limit);
        if (path is null)
        {
            return 2;
        }

        return SessionSummary.Print(source, path, settings.ShowContent, settings.Format);
    }

    private static async Task<string?> ResolvePathAsync(
        TrajectorySource source,
        string root,
        string? path,
        string? id,
        int limit)
    {
        if (!string.IsNullOrWhiteSpace(path))
        {
            return path;
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

        return match.Path;
    }
}

internal sealed class InteractiveCommand : AsyncCommand<GlobalSettings>
{
    public override async Task<int> ExecuteAsync(CommandContext context, GlobalSettings settings)
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
            else if (source == TrajectorySource.Ahp)
            {
                AnsiConsole.MarkupLine(
                    "[grey]AHP listing is Phase 3. Normalize a Shape A snapshot with:[/]");
                AnsiConsole.MarkupLine(
                    "[grey]  trajectory show --source ahp --path ./chat-export.json[/]");
            }
            else
            {
                AnsiConsole.MarkupLine("[grey]Create a session with the agent, or pass --root to another store.[/]");
            }

            if (AnsiConsole.Confirm("Normalize a transcript file by path instead?", defaultValue: false))
            {
                var filePath = AnsiConsole.Ask<string>("Path to transcript:");
                return SessionSummary.Print(source, filePath, settings.ShowContent, "both");
            }

            return 0;
        }

        var choices = page.Items
            .Select(FormatChoice)
            .ToList();
        choices.Add("(quit)");

        var selected = AnsiConsole.Prompt(
            new SelectionPrompt<string>()
                .Title($"Select a session ({page.Items.Count} shown)")
                .PageSize(Math.Min(20, choices.Count))
                .MoreChoicesText("[grey](move up/down)[/]")
                .AddChoices(choices));

        if (selected == "(quit)")
        {
            return 0;
        }

        var index = choices.IndexOf(selected);
        var listing = page.Items[index];
        AnsiConsole.WriteLine();
        return SessionSummary.Print(source, listing.Path, settings.ShowContent, "both");
    }

    private static string FormatChoice(TrajectoryListing item)
    {
        var updated = item.UpdatedAt?.ToString("u") ?? "—";
        var title = string.IsNullOrWhiteSpace(item.Title) ? string.Empty : $" | {item.Title}";
        var size = item.SizeBytes is null ? string.Empty : $" | {CliFormat.FormatBytes(item.SizeBytes.Value)}";
        return $"{item.Id} | {updated}{size}{title} | {item.Path}";
    }
}

internal static class SessionSummary
{
    public static int Print(TrajectorySource source, string path, bool showContent, string format)
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
            ir = TrajectoryConverter.NormalizeToIR(source, transcript);
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
