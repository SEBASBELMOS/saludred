import type { Candidate } from "../../api/types";

// Best first: same service and same IPS, then same service in another IPS,
// then other services (same IPS before other IPS). Ties keep the server order.
const tier = (c: Candidate) =>
  c.same_service ? (c.same_organization ? 0 : 1) : c.same_organization ? 2 : 3;
export const rankCandidates = (candidates: Candidate[]) =>
  candidates
    .map((candidate, index) => ({ candidate, index }))
    .sort((a, b) => tier(a.candidate) - tier(b.candidate) || a.index - b.index)
    .map(({ candidate }) => candidate);
/** The bed the form preselects: the first of the ranking, only if it serves the service asked. */
export const recommendedCandidate = (candidates: Candidate[]) => {
  const [best] = rankCandidates(candidates);
  return best?.same_service ? best : undefined;
};
