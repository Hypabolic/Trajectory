using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Hypabolic.Trajectory.Normalization;

internal static class NormalizationText
{
    private const int ArgumentLeafFloor = 2_000;

    public static (string Arguments, bool Reshaped, bool Truncated) ShrinkArguments(
        string? rawInput,
        int? limit)
    {
        var raw = string.IsNullOrEmpty(rawInput) ? "{}" : rawInput;
        JsonNode? parsed;
        try
        {
            parsed = JsonNode.Parse(raw);
        }
        catch (JsonException)
        {
            parsed = null;
        }

        if (parsed is not JsonObject)
        {
            var full = WrapRaw(raw);
            var wrapped = limit is null ? full : WrapRaw(raw, limit.Value);
            return (wrapped, true, !StringComparer.Ordinal.Equals(wrapped, full));
        }

        if (limit is null || CodePointLength(raw) <= limit.Value)
        {
            return (raw, false, false);
        }

        var legacyNode = JsonNode.Parse(raw)!;
        var legacy = ShrinkObjectLegacy(legacyNode, limit.Value);
        if (CodePointLength(legacy) <= limit.Value)
        {
            return (legacy, false, true);
        }

        var safeNode = JsonNode.Parse(raw)!;
        var safe = ShrinkObjectSafely(safeNode, limit.Value);
        return CodePointLength(safe) <= limit.Value
            ? (safe, false, true)
            : (WrapRaw(raw, limit.Value), true, true);
    }

    public static string TruncateResult(
        string text,
        int? limit,
        ToolResultTruncationStrategy strategy)
    {
        if (limit is null || CodePointLength(text) <= limit.Value)
        {
            return text;
        }

        var textLength = CodePointLength(text);
        var low = 0;
        var high = Math.Min(textLength - 1, limit.Value);
        var keep = -1;
        var marker = string.Empty;
        while (low <= high)
        {
            var candidateKeep = (low + high) / 2;
            var candidateMarker = TruncationMarker(textLength - candidateKeep);
            if (candidateKeep + CodePointLength(candidateMarker) <= limit.Value)
            {
                keep = candidateKeep;
                marker = candidateMarker;
                low = candidateKeep + 1;
            }
            else
            {
                high = candidateKeep - 1;
            }
        }

        if (keep < 0)
        {
            marker = SliceCodePoints("…", 0, limit.Value);
            keep = limit.Value - CodePointLength(marker);
        }

        if (strategy == ToolResultTruncationStrategy.Head)
        {
            return SliceCodePoints(text, 0, keep) + marker;
        }

        var headLength = (keep + 1) / 2;
        var tailLength = keep - headLength;
        return SliceCodePoints(text, 0, headLength) +
            marker +
            (tailLength > 0
                ? SliceCodePoints(text, textLength - tailLength, textLength)
                : string.Empty);
    }

    public static int CodePointLength(string value) => value.EnumerateRunes().Count();

    private static string ShrinkObjectLegacy(JsonNode root, int limit)
    {
        var leaves = CollectLeaves(root);
        var serialized = CanonicalJson.Compact(root);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        while (CodePointLength(serialized) > limit && leaves.Count > 0)
        {
            if (!seen.Add(serialized))
            {
                break;
            }

            var largest = leaves.MaxBy(static leaf => CodePointLength(leaf.Get()))!;
            var value = largest.Get();
            var valueLength = CodePointLength(value);
            if (valueLength <= ArgumentLeafFloor)
            {
                break;
            }

            var keep = Math.Max(ArgumentLeafFloor, valueLength / 2);
            largest.Set(SliceCodePoints(value, 0, keep) + TruncationMarker(valueLength - keep));
            serialized = CanonicalJson.Compact(root);
        }

        return serialized;
    }

    private static string ShrinkObjectSafely(JsonNode root, int limit)
    {
        var leaves = CollectLeaves(root);
        var serialized = CanonicalJson.Compact(root);
        while (CodePointLength(serialized) > limit)
        {
            var largest = leaves.Where(static leaf => leaf.CurrentLength > 0)
                .MaxBy(static leaf => leaf.CurrentLength);
            if (largest is null)
            {
                break;
            }

            var previousLength = CodePointLength(serialized);
            var overflow = previousLength - limit;
            var candidate = string.Empty;
            var nextKeep = 0;
            if (largest.Keep > 0)
            {
                var preferredFloor = largest.Keep > ArgumentLeafFloor ? ArgumentLeafFloor : 0;
                var markerBudget = CodePointLength(TruncationMarker(CodePointLength(largest.Original)));
                nextKeep = Math.Max(
                    preferredFloor,
                    Math.Min(
                        largest.Keep / 2,
                        largest.Keep - overflow - markerBudget - 1));
                nextKeep = Math.Max(0, Math.Min(nextKeep, largest.Keep - 1));
                candidate = SliceCodePoints(largest.Original, 0, nextKeep) +
                    TruncationMarker(CodePointLength(largest.Original) - nextKeep);
                if (CodePointLength(candidate) >= largest.CurrentLength)
                {
                    candidate = string.Empty;
                    nextKeep = 0;
                }
            }

            largest.Set(candidate);
            largest.Keep = nextKeep;
            largest.CurrentLength = CodePointLength(candidate);
            serialized = CanonicalJson.Compact(root);
            if (CodePointLength(serialized) >= previousLength && candidate.Length > 0)
            {
                largest.Set(string.Empty);
                largest.Keep = 0;
                largest.CurrentLength = 0;
                serialized = CanonicalJson.Compact(root);
            }
        }

        return serialized;
    }

    private static List<StringLeaf> CollectLeaves(JsonNode root)
    {
        var output = new List<StringLeaf>();
        Visit(root, output);
        return output;
    }

    private static void Visit(JsonNode node, List<StringLeaf> output)
    {
        if (node is JsonObject obj)
        {
            foreach (var property in obj.ToArray())
            {
                if (property.Value is JsonValue value &&
                    value.TryGetValue<string>(out var text))
                {
                    output.Add(new StringLeaf(
                        text,
                        () => obj[property.Key]!.GetValue<string>(),
                        replacement => obj[property.Key] = replacement));
                }
                else if (property.Value is not null)
                {
                    Visit(property.Value, output);
                }
            }
        }
        else if (node is JsonArray array)
        {
            for (var index = 0; index < array.Count; index++)
            {
                var captured = index;
                if (array[index] is JsonValue value &&
                    value.TryGetValue<string>(out var text))
                {
                    output.Add(new StringLeaf(
                        text,
                        () => array[captured]!.GetValue<string>(),
                        replacement => array[captured] = replacement));
                }
                else if (array[index] is { } child)
                {
                    Visit(child, output);
                }
            }
        }
    }

    private static string WrapRaw(string raw)
    {
        var wrapper = new JsonObject { ["_raw"] = raw };
        return CanonicalJson.Compact(wrapper);
    }

    private static string WrapRaw(string raw, int limit)
    {
        var full = WrapRaw(raw);
        if (CodePointLength(full) <= limit)
        {
            return full;
        }

        var low = 0;
        var rawLength = CodePointLength(raw);
        var high = Math.Min(rawLength, limit);
        var best = "{}";
        while (low <= high)
        {
            var keep = (low + high) / 2;
            var wrapper = new JsonObject
            {
                ["_raw"] = SliceCodePoints(raw, 0, keep) +
                    TruncationMarker(rawLength - keep),
            };
            var candidate = CanonicalJson.Compact(wrapper);
            if (CodePointLength(candidate) <= limit)
            {
                best = candidate;
                low = keep + 1;
            }
            else
            {
                high = keep - 1;
            }
        }

        return best;
    }

    private static string TruncationMarker(int remaining) =>
        $"\n… [truncated, {remaining} more chars]";

    private static string SliceCodePoints(string value, int start, int end)
    {
        var builder = new StringBuilder();
        var index = 0;
        foreach (var rune in value.EnumerateRunes())
        {
            if (index >= end)
            {
                break;
            }

            if (index >= start)
            {
                builder.Append(rune.ToString());
            }

            index++;
        }

        return builder.ToString();
    }

    private sealed class StringLeaf
    {
        public StringLeaf(string original, Func<string> get, Action<string> set)
        {
            Original = original;
            Get = get;
            Set = set;
            Keep = CodePointLength(original);
            CurrentLength = Keep;
        }

        public string Original { get; }
        public Func<string> Get { get; }
        public Action<string> Set { get; }
        public int Keep { get; set; }
        public int CurrentLength { get; set; }
    }
}
