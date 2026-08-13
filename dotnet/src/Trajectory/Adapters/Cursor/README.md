# Cursor source adapter

Cursor Agent stores `cursor-agent-transcript-jsonl` under
`~/.cursor/projects/<encoded-cwd>/agent-transcripts/<id>/<id>.jsonl`.
Transcripts have no native record or tool IDs; Trajectory uses location
identity. `store.db` is encrypted and is not v1 normalize input.

Listing identity is the session-directory UUID; callers following a listed
session lock `group_id` from the listing ID. An optional
`chats/*/<id>/meta.json` join supplies `title` and `updated_at`.
