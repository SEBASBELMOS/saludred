import { api } from "./client";
import type { CohortCluster } from "./types";

export const cohortApi = {
  clusters: (signal?: AbortSignal) =>
    api<CohortCluster[]>("/cohort/clusters", { signal }),
};
