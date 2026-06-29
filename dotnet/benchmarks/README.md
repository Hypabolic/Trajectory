# .NET benchmarks

`Trajectory.Benchmarks` is a dependency-free release-mode measurement harness.
It reports normalization throughput, managed bytes allocated on the current
thread, and deterministic output sizes for the shared Unicode/tool fixture.
The numbers are evidence for regressions, not a cross-runtime performance
contract.

```bash
dotnet run --project dotnet/benchmarks/Trajectory.Benchmarks/Trajectory.Benchmarks.csproj \
  -c Release
```
