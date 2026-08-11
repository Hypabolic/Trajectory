#![forbid(unsafe_code)]
#![doc = "Private versioned conformance protocol runner for the Rust implementation."]

use std::fs::{self, File};
use std::io::{self, Read as _};
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use chrono::DateTime;
use hypabolic_trajectory::{
    ListingOptions, NormalizeOptions, NormalizeRequest, SourceContext, TrajectoryError,
    TruncationStrategy, list_claude_code_trajectories, list_codex_trajectories,
    list_grok_build_trajectories, list_hermes_trajectories, list_openclaw_trajectories,
    list_pi_trajectories, normalize_claude_code, normalize_codex, normalize_grok_build,
    normalize_hermes, normalize_openclaw, normalize_pi, project_canonical, project_hypabolic,
    project_letta, project_minimal_jsonl, project_openai, project_opentelemetry,
};
use serde::Deserialize;
use serde_json::{Map, Value, json};

#[derive(Deserialize)]
struct Request {
    protocol_version: String,
    case: String,
    operation: String,
    repository_root: String,
}

#[derive(Deserialize)]
struct Manifest {
    id: String,
    source: String,
    transcript: String,
    operation: Map<String, Value>,
    store: Option<String>,
    listing: Option<ListingManifest>,
    #[serde(default)]
    source_context: SourceContextManifest,
    #[serde(default)]
    bounds: BoundsManifest,
    #[serde(default)]
    filters: FiltersManifest,
}

#[derive(Default, Deserialize)]
struct SourceContextManifest {
    group_id: Option<String>,
    base_byte_offset: Option<i64>,
    partial: Option<bool>,
    /// Spec allows boolean true OR string `"true"` (match .NET/TS).
    #[serde(default, deserialize_with = "deserialize_include_encrypted_reasoning")]
    include_encrypted_reasoning: Option<bool>,
}

fn deserialize_include_encrypted_reasoning<'de, D>(
    deserializer: D,
) -> Result<Option<bool>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = Option::<Value>::deserialize(deserializer)?;
    Ok(match value {
        None | Some(Value::Null) => None,
        Some(Value::Bool(flag)) => Some(flag),
        Some(Value::String(text)) => Some(text.eq_ignore_ascii_case("true")),
        Some(_) => Some(false),
    })
}

#[derive(Default, Deserialize)]
struct BoundsManifest {
    tool_arguments: Option<BoundManifest>,
    tool_results: Option<ResultBoundManifest>,
}

#[derive(Deserialize)]
struct BoundManifest {
    max_characters: Option<usize>,
}

#[derive(Deserialize)]
struct ResultBoundManifest {
    max_characters: Option<usize>,
    strategy: Option<String>,
}

#[derive(Default, Deserialize)]
struct FiltersManifest {
    tool_results: Option<String>,
}

#[derive(Deserialize)]
struct ListingManifest {
    limit: Option<usize>,
    all_pages: Option<bool>,
}

#[derive(Deserialize)]
struct Store {
    files: Vec<StoreFile>,
}

#[derive(Deserialize)]
struct StoreFile {
    path: String,
    content: String,
    updated_at: Option<String>,
}

fn main() {
    match run() {
        Ok(response) => {
            print!(
                "{}",
                serde_json::to_string(&response).expect("response is serializable")
            );
        }
        Err(error) => {
            print!(
                "{}",
                serde_json::to_string(&json!({
                    "case": "",
                    "operation": "",
                    "status": "protocol-error",
                    "output_text": null,
                    "diagnostics": [],
                    "fatal_error": {
                        "code": "invalid_request",
                        "message": error,
                    },
                }))
                .expect("response is serializable")
            );
            std::process::exit(2);
        }
    }
}

fn run() -> Result<Value, String> {
    let request = read_request()?;
    if request.protocol_version != "1" {
        return Err(format!(
            "Unsupported protocol version '{}'.",
            request.protocol_version
        ));
    }
    let repository_root = PathBuf::from(&request.repository_root);
    let cases_root = repository_root.join("conformance").join("cases");
    let case_directory = safe_join(&cases_root, &request.case)?;
    let manifest: Manifest = read_json(&case_directory.join("case.json"))?;
    if manifest.id != request.case {
        return Err("The requested case does not match its manifest ID.".into());
    }
    if !manifest.operation.contains_key(&request.operation) {
        return Err(format!(
            "Case '{}' does not declare operation '{}'.",
            request.case, request.operation
        ));
    }
    if !matches!(
        manifest.source.as_str(),
        "pi" | "claude-code" | "codex" | "openclaw" | "hermes" | "grok-build"
    ) {
        return Err(format!(
            "Rust does not support source '{}'.",
            manifest.source
        ));
    }

    let result = execute(
        &repository_root,
        &case_directory,
        &manifest,
        &request.operation,
    );
    match result {
        Ok((output_text, diagnostics)) => Ok(json!({
            "case": request.case,
            "operation": request.operation,
            "status": "success",
            "output_text": output_text,
            "diagnostics": diagnostics,
            "fatal_error": null,
        })),
        Err(error) => Ok(json!({
            "case": request.case,
            "operation": request.operation,
            "status": "fatal-error",
            "output_text": null,
            "diagnostics": [],
            "fatal_error": {
                "code": error.code,
                "message": error.message,
            },
        })),
    }
}

fn execute(
    repository_root: &Path,
    case_directory: &Path,
    manifest: &Manifest,
    operation: &str,
) -> Result<(String, Vec<hypabolic_trajectory::Diagnostic>), TrajectoryError> {
    if operation == "list-trajectories" {
        return execute_listing(repository_root, manifest).map(|value| (value, Vec::new()));
    }
    let transcript_path = safe_join(case_directory, &manifest.transcript)
        .map_err(|message| TrajectoryError::new("invalid_input", message))?;
    let transcript = fs::read(transcript_path)
        .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
    let strategy = manifest
        .bounds
        .tool_results
        .as_ref()
        .and_then(|value| value.strategy.as_deref())
        .map(|value| match value {
            "head" => Ok(TruncationStrategy::Head),
            "head-tail" => Ok(TruncationStrategy::HeadTail),
            _ => Err(TrajectoryError::new(
                "invalid_input",
                "Unknown tool result truncation strategy.",
            )),
        })
        .transpose()?;
    let normalize_request = NormalizeRequest {
        transcript: &transcript,
        source_context: SourceContext {
            group_id: manifest.source_context.group_id.as_deref(),
            base_byte_offset: manifest.source_context.base_byte_offset.unwrap_or(0),
            partial: manifest.source_context.partial.unwrap_or(false),
            include_encrypted_reasoning: manifest
                .source_context
                .include_encrypted_reasoning
                .unwrap_or(false),
        },
        options: NormalizeOptions {
            tool_arguments_max_characters: manifest
                .bounds
                .tool_arguments
                .as_ref()
                .map(|value| value.max_characters),
            tool_results_max_characters: manifest
                .bounds
                .tool_results
                .as_ref()
                .map(|value| value.max_characters),
            tool_results_strategy: strategy,
            include_tool_results: manifest
                .filters
                .tool_results
                .as_deref()
                .map(|value| value != "omit"),
        },
    };
    let trajectory = match manifest.source.as_str() {
        "pi" => normalize_pi(normalize_request),
        "claude-code" => normalize_claude_code(normalize_request),
        "codex" => normalize_codex(normalize_request),
        "openclaw" => normalize_openclaw(normalize_request),
        "hermes" => normalize_hermes(normalize_request),
        "grok-build" => normalize_grok_build(normalize_request),
        _ => unreachable!("source is validated before execution"),
    }?;
    let output = match operation {
        "normalize-letta" => project_letta(&trajectory),
        "normalize-canonical" => project_canonical(&trajectory),
        "normalize-hypabolic" => project_hypabolic(&trajectory),
        "project-openai" => project_openai(&trajectory),
        "project-minimal-jsonl" => project_minimal_jsonl(&trajectory),
        "project-otel" => project_opentelemetry(&trajectory),
        _ => Err(TrajectoryError::new(
            "unknown_operation",
            format!("Rust ML7 does not support operation '{operation}'."),
        )),
    }?;
    Ok((output, trajectory.diagnostics))
}

fn execute_listing(repository_root: &Path, manifest: &Manifest) -> Result<String, TrajectoryError> {
    let store_name = manifest
        .store
        .as_deref()
        .ok_or_else(|| TrajectoryError::new("invalid_input", "Listing case requires a store."))?;
    let store_path = safe_join(
        &repository_root.join("conformance").join("stores"),
        &format!("{store_name}/store.json"),
    )
    .map_err(|message| TrajectoryError::new("invalid_input", message))?;
    let store: Store =
        read_json(&store_path).map_err(|message| TrajectoryError::new("invalid_input", message))?;
    let root = unique_temp_root();
    fs::create_dir_all(&root)
        .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
    let outcome = (|| {
        for fixture in store.files {
            let destination = safe_join(&root, &fixture.path)
                .map_err(|message| TrajectoryError::new("invalid_input", message))?;
            if let Some(parent) = destination.parent() {
                fs::create_dir_all(parent)
                    .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
            }
            fs::write(&destination, fixture.content)
                .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
            if let Some(updated_at) = fixture.updated_at {
                set_modified_time(&destination, &updated_at)?;
            }
        }

        let listing = manifest.listing.as_ref();
        let limit = listing.and_then(|value| value.limit).unwrap_or(50);
        let all_pages = listing.and_then(|value| value.all_pages).unwrap_or(false);
        let mut pages = Vec::new();
        let mut cursor = None;
        loop {
            let listing_root =
                if matches!(manifest.source.as_str(), "claude-code" | "codex" | "grok-build") {
                    root.join("store")
                } else {
                    root.clone()
                };
            let options = ListingOptions {
                root: &listing_root,
                cursor: cursor.as_deref(),
                limit,
            };
            let page = match manifest.source.as_str() {
                "pi" => list_pi_trajectories(&options),
                "claude-code" => list_claude_code_trajectories(&options),
                "codex" => list_codex_trajectories(&options),
                "openclaw" => list_openclaw_trajectories(&options),
                "hermes" => list_hermes_trajectories(&options),
                "grok-build" => list_grok_build_trajectories(&options),
                _ => unreachable!("source is validated before execution"),
            }?;
            let items = page
                .items
                .iter()
                .map(|item| {
                    let relative = item.path.strip_prefix(&root).map_err(|_| {
                        TrajectoryError::new("invalid_input", "Listing escaped its explicit root.")
                    })?;
                    let mut obj = json!({
                        "id": item.id,
                        "path": format!("$ROOT/{}", relative.to_string_lossy().replace('\\', "/")),
                        "updated_at": item.updated_at,
                        "size_bytes": item.size_bytes,
                    });
                    if let Some(title) = &item.title {
                        obj.as_object_mut()
                            .expect("listing item object")
                            .insert("title".into(), json!(title));
                    }
                    Ok(obj)
                })
                .collect::<Result<Vec<_>, TrajectoryError>>()?;
            let next = page.next_cursor.clone();
            pages.push(json!({
                "items": items,
                "next_cursor": next,
            }));
            cursor = page.next_cursor;
            if !all_pages || cursor.is_none() {
                break;
            }
        }
        let output = if all_pages {
            Value::Array(pages)
        } else {
            pages.into_iter().next().expect("at least one page")
        };
        hypabolic_trajectory::serialize_projection(&output)
    })();
    let _ = fs::remove_dir_all(&root);
    outcome
}

fn read_request() -> Result<Request, String> {
    let mut text = String::new();
    let arguments = std::env::args_os().collect::<Vec<_>>();
    if arguments.len() == 2 {
        text = fs::read_to_string(&arguments[1]).map_err(|error| error.to_string())?;
    } else {
        io::stdin()
            .read_to_string(&mut text)
            .map_err(|error| error.to_string())?;
    }
    serde_json::from_str(&text).map_err(|error| error.to_string())
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let text = fs::read_to_string(path).map_err(|error| error.to_string())?;
    serde_json::from_str(&text).map_err(|error| error.to_string())
}

fn safe_join(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err("Fixture path escapes its declared root.".into());
    }
    Ok(root.join(path))
}

fn unique_temp_root() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "trajectory-conformance-{}-{nonce}",
        std::process::id()
    ))
}

fn set_modified_time(path: &Path, timestamp: &str) -> Result<(), TrajectoryError> {
    let milliseconds = DateTime::parse_from_rfc3339(timestamp)
        .map_err(|_| TrajectoryError::new("invalid_input", "Store timestamp is invalid."))?
        .timestamp_millis();
    let milliseconds = u64::try_from(milliseconds)
        .map_err(|_| TrajectoryError::new("invalid_input", "Store timestamp is out of range."))?;
    let time = UNIX_EPOCH + Duration::from_millis(milliseconds);
    let file = File::options()
        .write(true)
        .open(path)
        .map_err(|error| TrajectoryError::new("io_error", error.to_string()))?;
    file.set_times(fs::FileTimes::new().set_accessed(time).set_modified(time))
        .map_err(|error| TrajectoryError::new("io_error", error.to_string()))
}
