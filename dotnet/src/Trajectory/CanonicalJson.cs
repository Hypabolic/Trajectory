using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Hypabolic.Trajectory;

internal static class CanonicalJson
{
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        WriteIndented = false,
    };

    public static string Serialize(JsonNode? value) => Emit(Sort(value));

    public static string Compact(JsonNode value) => Emit(value);

    public static string Relaxed(JsonElement value)
    {
        var output = new StringBuilder();
        WriteElement(value, output);
        return output.ToString();
    }

    private static string Emit(JsonNode? value)
    {
        if (value is null)
        {
            return "null";
        }

        var output = new StringBuilder();
        WriteNode(value, output);
        return output.ToString();
    }

    private static void WriteNode(JsonNode value, StringBuilder output)
    {
        switch (value)
        {
            case JsonArray array:
                output.Append('[');
                for (var i = 0; i < array.Count; i++)
                {
                    if (i > 0) output.Append(',');
                    if (array[i] is { } item) WriteNode(item, output);
                    else output.Append("null");
                }

                output.Append(']');
                return;
            case JsonObject obj:
                output.Append('{');
                var first = true;
                foreach (var property in obj)
                {
                    if (!first) output.Append(',');
                    first = false;
                    WriteString(property.Key, output);
                    output.Append(':');
                    if (property.Value is { } item) WriteNode(item, output);
                    else output.Append("null");
                }

                output.Append('}');
                return;
            case JsonValue jsonValue when jsonValue.TryGetValue<string>(out var text):
                WriteString(text ?? string.Empty, output);
                return;
            default:
                output.Append(value.ToJsonString(SerializerOptions));
                return;
        }
    }

    private static void WriteElement(JsonElement value, StringBuilder output)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Null:
                output.Append("null");
                return;
            case JsonValueKind.True:
                output.Append("true");
                return;
            case JsonValueKind.False:
                output.Append("false");
                return;
            case JsonValueKind.Number:
                output.Append(value.GetRawText());
                return;
            case JsonValueKind.String:
                WriteString(value.GetString() ?? string.Empty, output);
                return;
            case JsonValueKind.Array:
                output.Append('[');
                var firstArrayItem = true;
                foreach (var item in value.EnumerateArray())
                {
                    if (!firstArrayItem) output.Append(',');
                    firstArrayItem = false;
                    WriteElement(item, output);
                }

                output.Append(']');
                return;
            case JsonValueKind.Object:
                output.Append('{');
                var firstProperty = true;
                foreach (var property in value.EnumerateObject())
                {
                    if (!firstProperty) output.Append(',');
                    firstProperty = false;
                    WriteString(property.Name, output);
                    output.Append(':');
                    WriteElement(property.Value, output);
                }

                output.Append('}');
                return;
            default:
                throw new JsonException($"Unsupported JSON value kind: {value.ValueKind}.");
        }
    }

    private static void WriteString(string value, StringBuilder output)
    {
        const string Hex = "0123456789ABCDEF";
        output.Append('"');
        foreach (var unit in value)
        {
            switch (unit)
            {
                case '"': output.Append("\\\""); break;
                case '\\': output.Append("\\\\"); break;
                case '\b': output.Append("\\b"); break;
                case '\t': output.Append("\\t"); break;
                case '\n': output.Append("\\n"); break;
                case '\f': output.Append("\\f"); break;
                case '\r': output.Append("\\r"); break;
                default:
                    if (unit <= 0x1F ||
                        (unit >= '\uE000' && unit <= '\uF8FF') ||
                        unit is '\u2028' or '\u2029' ||
                        (unit >= '\uD800' && unit <= '\uDFFF'))
                    {
                        output.Append("\\u");
                        output.Append(Hex[(unit >> 12) & 0xF]);
                        output.Append(Hex[(unit >> 8) & 0xF]);
                        output.Append(Hex[(unit >> 4) & 0xF]);
                        output.Append(Hex[unit & 0xF]);
                    }
                    else
                    {
                        output.Append(unit);
                    }

                    break;
            }
        }

        output.Append('"');
    }

    private static JsonNode? Sort(JsonNode? value)
    {
        if (value is JsonArray array)
        {
            var output = new JsonArray();
            foreach (var item in array)
            {
                output.Add(Sort(item));
            }

            return output;
        }

        if (value is JsonObject obj)
        {
            var output = new JsonObject();
            foreach (var property in obj.OrderBy(static item => item.Key, StringComparer.Ordinal))
            {
                output[property.Key] = Sort(property.Value);
            }

            return output;
        }

        return value?.DeepClone();
    }
}
