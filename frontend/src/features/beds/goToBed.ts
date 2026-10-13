import type { Bed } from "../../api/types";
import { normalize } from "../../components/format";

export type BedLookup =
  | { kind: "found"; bed: Bed }
  | { kind: "ambiguous"; count: number }
  | { kind: "missing" };
/**
 * Finds a bed from what a person types: the full code (N-HOS-01, any case) or,
 * when only one bed matches, a part of it (hos-01).
 */
export function findBedByCode(beds: Bed[], input: string): BedLookup {
  const query = normalize(input.trim());
  if (!query) return { kind: "missing" };
  const exact = beds.filter((bed) => normalize(bed.code) === query);
  if (exact.length === 1) return { kind: "found", bed: exact[0] };
  const matches = exact.length
    ? exact
    : beds.filter((bed) => normalize(bed.code).includes(query));
  if (matches.length === 1) return { kind: "found", bed: matches[0] };
  return matches.length
    ? { kind: "ambiguous", count: matches.length }
    : { kind: "missing" };
}
