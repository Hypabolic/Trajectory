let meta = #{
    name: "ahp-ingest",
    description: "Implement AHP (Agent Host Protocol) ingest source: contracts, multi-runtime snapshot decoder, conformance, CLI wiring; review/fix; leave branch ready for merge",
    when_to_use: "When implementing docs/ahp-source-spec.md Phase 0–1 AHP snapshot ingest",
    phases: [
        #{ title: "Contracts and fixtures", detail: "AHP-0: source contract, export schema, synthetic fixtures" },
        #{ title: "Multi-runtime implement", detail: "Snapshot decoder on .NET, TypeScript, Rust + runners" },
        #{ title: "Verify", detail: "Build, unit tests, shared conformance for ahp cases" },
        #{ title: "Review and fix", detail: "Adversarial review against ahp-source-spec; implementer fixes" },
        #{ title: "Final package", detail: "Advertise only when all three runtimes pass; docs + summary" },
    ],
};

let summary_schema = #{
    "type": "object",
    "required": ["status", "summary", "files_changed"],
    "properties": #{
        "status": #{ "type": "string", "enum": ["done", "blocked", "partial"] },
        "summary": #{ "type": "string" },
        "files_changed": #{ "type": "array", "items": #{ "type": "string" }, "maxItems": 120 },
        "notes": #{ "type": "string" },
    },
};

let review_schema = #{
    "type": "object",
    "required": ["open_issues", "verdict", "issues"],
    "properties": #{
        "verdict": #{ "type": "string", "enum": ["pass", "fail"] },
        "open_issues": #{ "type": "integer", "minimum": 0 },
        "issues": #{
            "type": "array",
            "maxItems": 25,
            "items": #{
                "type": "object",
                "required": ["severity", "file", "description"],
                "properties": #{
                    "severity": #{ "type": "string", "enum": ["bug", "suggestion", "nit"] },
                    "file": #{ "type": "string" },
                    "description": #{ "type": "string" },
                    "suggestion": #{ "type": "string" },
                },
            },
        },
        "summary": #{ "type": "string" },
    },
};

let branch = "feature/ahp-ingest";
if args != () && args.branch != () {
    let b = args.branch;
    if type_of(b) == "string" {
        b.trim();
        if b != "" {
            branch = b;
        }
    }
}

// ---------------------------------------------------------------------------
// Phase 1 — Contracts and fixtures (AHP-0)
// ---------------------------------------------------------------------------
phase("Contracts and fixtures");
log("AHP-0: contracts, export schema, synthetic conformance fixtures on " + branch);

let p0 = "";
p0 += "You are the Trajectory implementer on branch " + branch + ".\n\n";
p0 += "GOAL: Land AHP-0 from docs/ahp-source-spec.md — contracts and fixtures ONLY.\n";
p0 += "Do NOT implement runtime decoders yet (that is the next phase).\n\n";
p0 += "READ FIRST (use read_file/grep):\n";
p0 += "- docs/ahp-source-spec.md (full design; Phase 0–1)\n";
p0 += "- docs/adapter-authoring.md\n";
p0 += "- contracts/spec/normalization.md, identity.md, listing.md\n";
p0 += "- contracts/compatibility.json and an existing source case under conformance/cases/ (e.g. hermes or pi) as layout template\n";
p0 += "- contracts/schemas/ for schema style\n\n";
p0 += "DELIVERABLES:\n";
p0 += "1. contracts/spec/sources/ahp.md — normative decode/mapping for wire source `ahp` (snapshot Shape A; note action-log deferred).\n";
p0 += "2. contracts/schemas/ahp-export-v1.schema.json — Trajectory export envelope: ahpProtocolVersion, chat (ChatState-like), optional session.\n";
p0 += "3. conformance/vendor/ahp/PROTOCOL_VERSION containing e.g. 0.3.0 and a short README noting schema pin.\n";
p0 += "4. Synthetic sanitized fixtures under conformance/cases/ahp/:\n";
p0 += "   - tool-calls/  (user turn + markdown + toolCall complete)\n";
p0 += "   - multi-turn/  (two completed turns)\n";
p0 += "   - cancelled-turn/ (cancelled or error turn; no invented success)\n";
p0 += "   Each case: case.json + input.json (Shape A snapshot) + expected placeholders OR expected files only if you can generate from a stub.\n";
p0 += "   Prefer authoring input.json fully; expected.*.json may be stubs marked for fill-in in Phase 2 if no decoder exists yet — but case.json must be valid.\n";
p0 += "5. Privacy: synthetic URIs (ahp-chat:/00000000-...), fake titles, no real paths/secrets.\n";
p0 += "6. Update docs/ahp-source-spec.md status note that AHP-0 landed if useful.\n\n";
p0 += "Do NOT add ahp to compatibility.json implemented.sources yet.\n";
p0 += "Commit on " + branch + " with conventional commits. Do not push.\n";
p0 += "Use file tools for writes; shell for git and read-only inspection.\n";

let r0 = agent(p0, #{
    label: "ahp-0-contracts",
    capability_mode: "all",
    isolation_worktree: false,
    output_schema: summary_schema,
});

if r0 == () || !r0.success {
    pause("infra", "AHP-0 contracts implementer failed — inspect and resume.");
}
if r0.output != () && r0.output.status != () {
    log("AHP-0 status: " + r0.output.status);
}

// ---------------------------------------------------------------------------
// Phase 2 — Multi-runtime snapshot implement
// ---------------------------------------------------------------------------
phase("Multi-runtime implement");
log("Implementing AHP snapshot decoder on .NET, TypeScript, and Rust");

let p1 = "";
p1 += "You are the Trajectory implementer on branch " + branch + ".\n\n";
p1 += "GOAL: Phase 1 of docs/ahp-source-spec.md — **snapshot (Shape A) source adapter** for wire name `ahp` on ALL three runtimes.\n\n";
p1 += "AUTHORITATIVE:\n";
p1 += "- docs/ahp-source-spec.md sections 4–6, 11\n";
p1 += "- contracts/spec/sources/ahp.md (from prior phase)\n";
p1 += "- Existing adapters: Hermes/OpenClaw/Claude as patterns (decode only; normalizer owns linking/bounds)\n";
p1 += "- AHP ChatState model: turns[], activeTurn, Message, responseParts (markdown|toolCall|reasoning|…)\n\n";
p1 += "IMPLEMENT:\n";
p1 += "1. Decode Shape A JSON: { ahpProtocolVersion?, chat: ChatState, session? }\n";
p1 += "2. Map turns → user message + response parts → tool calls/results → assistant text; GroupId = chat URI\n";
p1 += "3. Wire source name exactly `ahp`\n";
p1 += "4. .NET: Adapters/Ahp/… + registry; TypeScript: internal source + exports; Rust: normalize_ahp + listing stub if needed\n";
p1 += "5. Conformance runners accept `ahp`; fill expected goldens by generating candidates, hand-review, commit expected files\n";
p1 += "6. Sample CLIs: allow --source ahp for show --path (listing may be explicit-root only later)\n";
p1 += "7. Unit tests for happy path + cancelled turn\n";
p1 += "8. When all three runtimes pass shared ahp cases: add ahp to compatibility.json implemented.sources and runtime-capabilities manifests\n";
p1 += "9. Update identity-baseline.sha256 if identity goldens added\n";
p1 += "10. README sources table + adapter-authoring mention\n\n";
p1 += "OUT OF SCOPE: action-log reduce (Shape B), live WebSocket host, AHP output projection, SQLite.\n";
p1 += "Normalizer contract remains 0.2.0; package versions stay synchronized (do not bump unless required).\n";
p1 += "TESTS: run relevant unit tests and python3 conformance/verify.py --source ahp for each runner.\n";
p1 += "Commit on " + branch + ". Do not push.\n";

let r1 = agent(p1, #{
    label: "ahp-1-runtimes",
    capability_mode: "all",
    isolation_worktree: false,
    output_schema: summary_schema,
});

if r1 == () || !r1.success {
    pause("infra", "Multi-runtime AHP implementer failed — inspect and resume.");
}
if r1.output != () && r1.output.status != () {
    log("Runtime implement status: " + r1.output.status);
}

// ---------------------------------------------------------------------------
// Phase 3 — Verify
// ---------------------------------------------------------------------------
phase("Verify");
log("Build and conformance verification for AHP");

let p2 = "";
p2 += "You are a verification agent on branch " + branch + ". READ-WRITE only to fix broken tests/builds related to AHP.\n\n";
p2 += "RUN and FIX until green (or document precise blockers):\n";
p2 += "1. dotnet test for Trajectory tests + build conformance runner; verify.py --source ahp with .NET runner\n";
p2 += "2. typescript: npm test / build; verify.py --source ahp with node runner\n";
p2 += "3. rust: cargo test + clippy -D warnings for affected crates; verify.py --source ahp with rust runner\n";
p2 += "4. If ahp is in compatibility.implemented.sources, full verify.py without --source must still pass\n";
p2 += "5. Ensure contracts/spec/sources/ahp.md matches implementation\n\n";
p2 += "Use file tools for any golden/code fixes. Commit fixes on " + branch + ".\n";
p2 += "Report what commands passed/failed.\n";

let r2 = agent(p2, #{
    label: "ahp-verify",
    capability_mode: "all",
    isolation_worktree: false,
    output_schema: summary_schema,
});

if r2 == () || !r2.success {
    pause("verification", "AHP verify agent failed — resume after env/tooling check.");
}
if r2.output != () && r2.output.status != () {
    log("Verify status: " + r2.output.status);
}

// ---------------------------------------------------------------------------
// Phase 4 — Review and fix
// ---------------------------------------------------------------------------
phase("Review and fix");
log("Adversarial review against ahp-source-spec");

let review_prompt = "";
review_prompt += "You are a strict code reviewer. Use read-only tools (read_file, grep, git diff) — inspect the actual tree.\n\n";
review_prompt += "Review AHP ingest on branch " + branch + " against docs/ahp-source-spec.md Phase 0–1.\n\n";
review_prompt += "Check:\n";
review_prompt += "- Wire source is `ahp`; unit is chat; Shape A decode present on .NET/TS/Rust\n";
review_prompt += "- Mapping: turns, tools, cancel/error; no invented usage/model\n";
review_prompt += "- No live WebSocket/host client in core packages\n";
review_prompt += "- Conformance cases + goldens exist and verify\n";
review_prompt += "- Privacy of fixtures; diagnostics content-safe\n";
review_prompt += "- compatibility.json only lists ahp if all runtimes pass\n";
review_prompt += "- No AHP output projection (ingest only)\n\n";
review_prompt += "Return verdict pass only if production-ready for Phase 1. List concrete issues with file paths.\n";

let rev = agent(review_prompt, #{
    label: "ahp-review",
    capability_mode: "read-only",
    isolation_worktree: false,
    output_schema: review_schema,
});

if rev == () || !rev.success {
    pause("verification", "Reviewer agent failed.");
}

let need_fix = true;
if rev.output != () && rev.output.verdict != () && rev.output.open_issues != () {
    log("Review open_issues=" + rev.output.open_issues.to_string() + " verdict=" + rev.output.verdict);
    if rev.output.verdict == "pass" && rev.output.open_issues == 0 {
        need_fix = false;
    }
}

if need_fix {
    let fix_prompt = "";
    fix_prompt += "You are the Trajectory implementer on " + branch + ". Fix ALL open review issues below.\n\n";
    if rev.output != () && rev.output.summary != () {
        fix_prompt += "Review summary: " + rev.output.summary + "\n\n";
    }
    if rev.output != () && rev.output.issues != () {
        fix_prompt += "Issues JSON: " + json_encode(rev.output.issues) + "\n\n";
    }
    fix_prompt += "Re-run relevant tests/conformance after fixes. Commit on " + branch + ". Do not push.\n";
    fix_prompt += "Stay within AHP ingest Phase 1 scope.\n";

    let fix = agent(fix_prompt, #{
        label: "ahp-fix",
        capability_mode: "all",
        isolation_worktree: false,
        output_schema: summary_schema,
    });

    if fix == () || !fix.success {
        pause("infra", "AHP fix implementer failed.");
    }
    if fix.output != () && fix.output.status != () {
        log("Fix status: " + fix.output.status);
    }

    // Second review pass
    let rev2 = agent(review_prompt + "\n\nRe-review after fixes. Prior issues should be closed or re-filed if still broken.\n", #{
        label: "ahp-rereview",
        capability_mode: "read-only",
        isolation_worktree: false,
        output_schema: review_schema,
    });

    if rev2 != () && rev2.success && rev2.output != () {
        let oi = 0;
        let vd = "";
        if rev2.output.open_issues != () { oi = rev2.output.open_issues; }
        if rev2.output.verdict != () { vd = rev2.output.verdict; }
        log("Re-review open_issues=" + oi.to_string() + " verdict=" + vd);
        if vd != "pass" || oi > 0 {
            let fix2_prompt = "";
            fix2_prompt += "Final fix round on " + branch + ". Remaining issues:\n";
            if rev2.output.issues != () {
                fix2_prompt += json_encode(rev2.output.issues) + "\n";
            }
            if rev2.output.summary != () {
                fix2_prompt += "Summary: " + rev2.output.summary + "\n";
            }
            fix2_prompt += "Fix bugs fully; nits if cheap. Re-verify ahp conformance. Commit.\n";
            let fix2 = agent(fix2_prompt, #{
                label: "ahp-fix-2",
                capability_mode: "all",
                isolation_worktree: false,
                output_schema: summary_schema,
            });
            if fix2 == () || !fix2.success {
                pause("infra", "Second fix round failed.");
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Phase 5 — Final package
// ---------------------------------------------------------------------------
phase("Final package");
log("Docs + implementation summary for AHP ingest");

let p_final = "";
p_final += "You are the Trajectory implementer on " + branch + ".\n";
p_final += "Finalize AHP Phase 1 packaging:\n";
p_final += "1. Ensure docs/ahp-source-spec.md notes Phase 0–1 status\n";
p_final += "2. README source table includes AHP if advertised\n";
p_final += "3. Write a concise IMPLEMENTATION summary to docs/ahp-ingest-status.md (what shipped, how to test, known gaps vs Shape B/live)\n";
p_final += "4. git status clean of WIP except intentional files; commit any remaining docs\n";
p_final += "5. List final files_changed and test commands that pass\n";
p_final += "Do not push; do not merge to main/develop.\n";

let r_final = agent(p_final, #{
    label: "ahp-finalize",
    capability_mode: "all",
    isolation_worktree: false,
    output_schema: summary_schema,
});

if r_final == () || !r_final.success {
    pause("infra", "Finalize agent failed.");
}

fn out_summary(r) {
    if r == () || !r.success || r.output == () || r.output.summary == () {
        return "(no summary)";
    }
    r.output.summary
}

fn out_status(r) {
    if r == () || !r.success || r.output == () || r.output.status == () {
        return "unknown";
    }
    r.output.status
}

let report = "";
report += "# AHP ingest workflow result\n\n";
report += "Branch: " + branch + "\n\n";
report += "## Phase summaries\n";
report += "- AHP-0: " + out_summary(r0) + "\n";
report += "- Runtimes: " + out_summary(r1) + "\n";
report += "- Verify: " + out_summary(r2) + "\n";
report += "- Final: " + out_summary(r_final) + "\n";

let path = write_scratch_file("ahp-ingest-report.md", report);
complete(#{
    path: path,
    branch: branch,
    status: out_status(r_final),
    report: report,
});
