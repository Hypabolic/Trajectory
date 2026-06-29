# TypeScript benchmarks

The release-mode harness reports normalization throughput, heap delta, and
deterministic output sizes. Run it after building the workspace:

```bash
cd typescript
npm run benchmark
```

Measurements are implementation-specific regression evidence, not a
cross-runtime performance contract.
