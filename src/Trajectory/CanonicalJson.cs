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

    public static string Serialize(JsonNode? value) =>
        Sort(value)?.ToJsonString(SerializerOptions) ?? "null";

    public static string Compact(JsonNode value) => value.ToJsonString(SerializerOptions);

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
