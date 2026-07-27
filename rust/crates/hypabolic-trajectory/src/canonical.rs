use std::cmp::Ordering;
use std::fmt::Write as _;

use serde_json::Value;

use crate::TrajectoryError;

/// Serializes a JSON value using Trajectory's canonical JSON 0.2.0 algorithm.
///
/// Object keys are ordered by unsigned UTF-16 code units. This is deliberately
/// not RFC 8785/JCS.
pub fn canonical_json(value: &Value) -> Result<String, TrajectoryError> {
    serialize(value, true)
}

/// Serializes compact JSON while retaining object insertion order.
pub fn relaxed_json(value: &Value) -> Result<String, TrajectoryError> {
    serialize(value, false)
}

fn serialize(value: &Value, sort_objects: bool) -> Result<String, TrajectoryError> {
    let mut output = String::new();
    write_value(&mut output, value, sort_objects)?;
    Ok(output)
}

fn write_value(
    output: &mut String,
    value: &Value,
    sort_objects: bool,
) -> Result<(), TrajectoryError> {
    match value {
        Value::Null => output.push_str("null"),
        Value::Bool(value) => output.push_str(if *value { "true" } else { "false" }),
        Value::Number(value) => output.push_str(&value.to_string()),
        Value::String(value) => write_json_string(output, value),
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write_value(output, value, sort_objects)?;
            }
            output.push(']');
        }
        Value::Object(values) => {
            output.push('{');
            let mut entries = values.iter().collect::<Vec<_>>();
            if sort_objects {
                entries.sort_by(|left, right| utf16_compare(left.0, right.0));
            }
            for (index, (key, value)) in entries.into_iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write_json_string(output, key);
                output.push(':');
                write_value(output, value, sort_objects)?;
            }
            output.push('}');
        }
    }
    Ok(())
}

pub(crate) fn write_json_string(output: &mut String, value: &str) {
    output.push('"');
    for unit in value.encode_utf16() {
        match unit {
            0x22 => output.push_str("\\\""),
            0x5c => output.push_str("\\\\"),
            0x08 => output.push_str("\\b"),
            0x09 => output.push_str("\\t"),
            0x0a => output.push_str("\\n"),
            0x0c => output.push_str("\\f"),
            0x0d => output.push_str("\\r"),
            0x00..=0x1f | 0xe000..=0xf8ff | 0x2028 | 0x2029 | 0xd800..=0xdfff => {
                let _ = write!(output, "\\u{unit:04X}");
            }
            _ => {
                if let Some(character) = char::from_u32(u32::from(unit)) {
                    output.push(character);
                }
            }
        }
    }
    output.push('"');
}

pub(crate) fn utf16_compare(left: &str, right: &str) -> Ordering {
    left.encode_utf16().cmp(right.encode_utf16())
}

#[cfg(test)]
mod tests {
    use serde_json::{Map, Value};

    use super::{canonical_json, relaxed_json};

    #[test]
    fn sorts_utf16_and_uses_contract_escaping() {
        let mut value = Map::new();
        value.insert("\u{10000}".into(), Value::String("😀".into()));
        value.insert("\u{e000}".into(), Value::String("\u{2028}".into()));
        assert_eq!(
            canonical_json(&Value::Object(value.clone())).unwrap(),
            r#"{"\uD800\uDC00":"\uD83D\uDE00","\uE000":"\u2028"}"#
        );
        assert_eq!(
            relaxed_json(&Value::Object(value)).unwrap(),
            r#"{"\uD800\uDC00":"\uD83D\uDE00","\uE000":"\u2028"}"#
        );
    }
}
