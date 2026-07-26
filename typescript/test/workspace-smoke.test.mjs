import assert from "node:assert/strict";
import test from "node:test";

import {
  NORMALIZER_CONTRACT_VERSION,
  transcriptBytes,
} from "@hypabolic/trajectory";
import * as TrajectoryNode from "@hypabolic/trajectory-node";
import { OTEL_GENAI_SCHEMA_VERSION } from "@hypabolic/trajectory-otel";
import { CONFORMANCE_PROTOCOL_VERSION } from "@hypabolic/trajectory-testing";

test("workspace packages install and import through public exports", () => {
  assert.equal(NORMALIZER_CONTRACT_VERSION, "0.2.0");
  assert.equal(CONFORMANCE_PROTOCOL_VERSION, "1");
  assert.equal(OTEL_GENAI_SCHEMA_VERSION, "1");
  assert.deepEqual(Object.keys(TrajectoryNode), []);
  assert.deepEqual(
    [...transcriptBytes("😀")],
    [0xf0, 0x9f, 0x98, 0x80],
  );
});
