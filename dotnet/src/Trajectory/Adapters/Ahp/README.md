# AHP source adapter

Agent Host Protocol (`ahp`) offline snapshot ingest (Shape A).

Accepted container:

```json
{
  "ahpProtocolVersion": "0.7.0",
  "chat": { /* ChatState */ },
  "session": { /* optional provenance */ }
}
```

Wire source name is always `ahp`. GroupId is the chat URI (`chat.resource`).

Action-log reduce (Shape B) and export-directory listing are later phases.
Normalization operates on one chat snapshot per call.
