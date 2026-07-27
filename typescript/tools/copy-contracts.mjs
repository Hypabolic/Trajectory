import { cp, mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const destination = resolve(workspace, "packages/trajectory/contracts");
await rm(destination, { force: true, recursive: true });
await mkdir(destination, { recursive: true });
await cp(
  resolve(workspace, "../contracts/compatibility.json"),
  resolve(destination, "compatibility.json"),
);
await cp(
  resolve(workspace, "../contracts/schemas"),
  resolve(destination, "schemas"),
  { recursive: true },
);
