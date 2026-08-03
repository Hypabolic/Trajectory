using Hypabolic.Trajectory.Listing;

namespace Hypabolic.Trajectory.Adapters.Ahp;

/// <summary>
/// Phase 1 listing stub for AHP export directories.
/// Full export-tree discovery lands in Phase 3; missing/unknown roots list empty.
/// </summary>
internal sealed class AhpTrajectoryLister : ITrajectoryLister
{
    public TrajectorySource Source => TrajectorySource.Ahp;

    public IReadOnlyList<TrajectoryListing> List(string? root)
    {
        // Phase 1: snapshot normalize only. Explicit-root export layout listing
        // is Phase 3; return empty so show --path remains the supported path.
        _ = root;
        return [];
    }
}
