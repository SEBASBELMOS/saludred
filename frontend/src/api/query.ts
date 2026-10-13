import { QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "./client";
import { loadNetwork } from "./capacity";
import { queueApi } from "./queue";
import { bedsApi } from "./beds";
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (count, error) =>
        !(
          error instanceof ApiError &&
          error.status >= 400 &&
          error.status < 500
        ) && count < 1,
    },
    mutations: { retry: false },
  },
});
export const useNetwork = () =>
  useQuery({
    queryKey: ["network"],
    queryFn: ({ signal }) => loadNetwork(signal),
  });
export const useQueue = () =>
  useQuery({
    queryKey: ["queue"],
    queryFn: ({ signal }) => queueApi.list(signal),
  });
// Optional signal: when it fails the screen hides the group, so it never retries or reports.
export const useOverdueCleaning = (enabled: boolean, minutes = 60) =>
  useQuery({
    queryKey: ["cleaning-overdue", minutes],
    queryFn: ({ signal }) => bedsApi.overdueCleaning(minutes, signal),
    enabled,
    retry: false,
    meta: { silent: true },
  });
export function useInvalidateClinical() {
  const client = useQueryClient();
  return () =>
    client.invalidateQueries({
      predicate: (query) => query.queryKey[0] !== "session",
    });
}
