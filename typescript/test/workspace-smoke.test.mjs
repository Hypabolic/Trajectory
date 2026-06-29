import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  ImplementedSources,
  NORMALIZER_CONTRACT_VERSION,
  OutputSchemaIds,
  transcriptBytes,
} from "@hypabolic/trajectory";
import * as TrajectoryNode from "@hypabolic/trajectory-node";
import { OTEL_GENAI_SCHEMA_VERSION } from "@hypabolic/trajectory-otel";
import { CONFORMANCE_PROTOCOL_VERSION } from "@hypabolic/trajectory-testing";

test("workspace packages install and import through public exports", () => {
  assert.equal(NORMALIZER_CONTRACT_VERSION, "0.2.0");
  assert.equal(CONFORMANCE_PROTOCOL_VERSION, "1");
  assert.equal(OTEL_GENAI_SCHEMA_VERSION, "1");
  assert.deepEqual(Object.keys(TrajectoryNode), [
    "listClaudeCodeTrajectories",
    "listCodexTrajectories",
    "listPiTrajectories",
    "listTrajectories",
  ]);
  assert.deepEqual(
    [...transcriptBytes("😀")],
    [0xf0, 0x9f, 0x98, 0x80],
  );
});

test("runtime capabilities match the authoritative compatibility manifest", async () => {
  const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const capabilities = JSON.parse(
    await readFile(
      resolve(workspace, "packages/trajectory/runtime-capabilities.json"),
      "utf8",
    ),
  );
  const compatibility = JSON.parse(
    await readFile(resolve(workspace, "../contracts/compatibility.json"), "utf8"),
  );

  assert.equal(capabilities.runtime, "typescript");
  assert.equal(capabilities.slice, "ML7");
  assert.equal(capabilities.normalizer_contract_version, NORMALIZER_CONTRACT_VERSION);
  assert.deepEqual(capabilities.sources, [...ImplementedSources]);
  assert.deepEqual(capabilities.sources, compatibility.implemented.sources);
  assert.deepEqual(
    capabilities.outputs,
    compatibility.implemented.outputs,
  );
  assert.deepEqual(
    capabilities.capabilities,
    compatibility.capabilities.required,
  );
  assert.deepEqual(
    capabilities.outputs.filter((output) => output !== "otel-genai-spans-v1"),
    Object.values(OutputSchemaIds),
  );
});
