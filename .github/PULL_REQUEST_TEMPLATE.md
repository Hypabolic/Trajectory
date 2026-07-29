<!--
Thank you for contributing to Trajectory.

Behaviour changes must stay aligned across .NET, TypeScript, and Rust, with
shared evidence under contracts/ and conformance/. See docs/contributing.md.
Sanitize all fixtures—never commit secrets or personal transcripts.
-->

## Summary

<!-- What does this PR change and why? Link related issues: Fixes #123 -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature / adapter / projection
- [ ] Breaking change (API or wire/contract)
- [ ] Documentation only
- [ ] Chore (CI, packaging, refactor with no behaviour change)
- [ ] Release / version bump

## Runtimes touched

- [ ] .NET
- [ ] TypeScript
- [ ] Rust
- [ ] Contracts / conformance only
- [ ] Docs / CI only

## How tested

<!-- e.g. unit tests, shared conformance for source X, sample CLI browse -->

```bash
# paste the commands you ran, or note CI coverage
```

## Checklist

- [ ] I read [docs/contributing.md](../docs/contributing.md)
- [ ] Behaviour change includes or updates a **shared conformance** case when applicable
- [ ] Goldens are hand-reviewed (CI never regenerate-and-accept in one step)
- [ ] Multi-runtime parity for source/output/capability changes (or an explicit temporary gap is documented and not advertised)
- [ ] `contracts/compatibility.json` and runtime `runtime-capabilities.json` stay in sync when capabilities change
- [ ] Diagnostics remain content-safe (no transcript secrets in messages)
- [ ] Identity-bearing bytes unchanged under the current normalizer contract, **or** a contract version bump is included
- [ ] Fixtures are sanitized
- [ ] Docs / README / CHANGELOG updated when user-facing
- [ ] Sample CLIs still build if listing or normalize surfaces changed

## Notes for reviewers

<!-- Risk areas, intentional gaps, follow-ups -->
