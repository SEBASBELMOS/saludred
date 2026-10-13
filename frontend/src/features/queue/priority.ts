import type { QueuedRequest } from "../../api/types";
// Same ranks as PRIORITY_RANK in app/services/beds.py: the higher is served first.
const rank = { EMERGENCY: 3, URGENT: 2, ROUTINE: 1 };
// Sort key equivalent to _queue_key in the API; the smaller key is served first:
// clinical priority, then criticality (higher first, no score last), then arrival.
export function queueKey(
  request: QueuedRequest,
): [number, number, number] {
  const score = request.criticality_score;
  return [
    -rank[request.priority],
    // The score is at most 1, so 1 places a request without score after any with one.
    score == null ? 1 : -score,
    new Date(request.requested_at).getTime(),
  ];
}
// Lexicographic comparison of two keys: negative when `a` goes before `b`.
export function compareQueue(a: QueuedRequest, b: QueuedRequest) {
  const left = queueKey(a);
  const right = queueKey(b);
  for (let index = 0; index < left.length; index++)
    if (left[index] !== right[index]) return left[index] - right[index];
  return 0;
}
// The criticality score (0 to 1) as the percentage the queue shows; a dash when the
// AI service has not scored the patient yet.
export const criticalityPercent = (score?: number | null) =>
  score == null ? "—" : `${Math.round(score * 100)} %`;
// The request that the API would serve before this one, if any. Mirrors
// _more_urgent_waiting: only requests that compete for the same bed (same service and,
// when this one has a destination, the same destination) are compared.
export function priorRequest(request: QueuedRequest, queue: QueuedRequest[]) {
  return queue
    .filter(
      (other) =>
        other.id !== request.id &&
        other.required_service === request.required_service &&
        (!request.target_organization_id ||
          other.target_organization_id === request.target_organization_id) &&
        compareQueue(other, request) < 0,
    )
    .sort(compareQueue)[0];
}
