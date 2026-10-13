import { api } from "./client";
import type { NetworkCapacity, Bed } from "./types";
export async function loadNetwork(signal?: AbortSignal) {
  const network = await api<NetworkCapacity>("/network/capacity", { signal });
  const lists = await Promise.all(
    network.organizations.map((org) =>
      api<Bed[]>(`/organizations/${org.organization_id}/beds`, { signal }),
    ),
  );
  return { network, beds: lists.flat() };
}
export type NetworkSnapshot = Awaited<ReturnType<typeof loadNetwork>>;
