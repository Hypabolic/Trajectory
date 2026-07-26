/**
 * ML2 starts from exact transcript bytes. String input is a convenience only;
 * adapters derive byte anchors from this UTF-8 encoding, never UTF-16 indices.
 */
export type TranscriptInput = Uint8Array | string;

export interface SourceContext {
  readonly groupId?: string;
  readonly baseByteOffset?: bigint;
  readonly partial?: boolean;
}

export interface NormalizeRequest {
  readonly source: "pi";
  readonly transcript: TranscriptInput;
  readonly sourceContext?: SourceContext;
}

export interface TrajectoryDiagnostic {
  readonly code: string;
  readonly message: string;
  readonly inputLine?: number;
  readonly recordIndex?: number;
  readonly count?: number;
}

export const NORMALIZER_CONTRACT_VERSION = "0.2.0";

export function transcriptBytes(input: TranscriptInput): Uint8Array {
  return typeof input === "string" ? new TextEncoder().encode(input) : input;
}
