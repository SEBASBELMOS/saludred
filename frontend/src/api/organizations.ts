import { api } from "./client";
import type { Organization, Page } from "./types";
export const listEps = (signal?: AbortSignal) =>
  api<Page<Organization>>("/organizations?organization_type=EPS&page_size=20", {
    signal,
  });

export interface OrganizationFields {
  code: string;
  name: string;
}
export interface OrganizationVersion {
  version_number: number;
  snapshot_json: Record<string, unknown>;
  changed_fields: string[] | null;
  changed_by: string | null;
  changed_at: string;
}
export const organizationsApi = {
  eps: (signal?: AbortSignal) =>
    api<Page<Organization>>(
      "/organizations?organization_type=EPS&page_size=100",
      { signal },
    ),
  ips: (epsId: string, signal?: AbortSignal) =>
    api<Organization[]>(`/eps/${epsId}/ips`, { signal }),
  createEps: (body: OrganizationFields) =>
    api<Organization>("/eps", { method: "POST", body }),
  createIps: (epsId: string, body: OrganizationFields) =>
    api<Organization>(`/eps/${epsId}/ips`, { method: "POST", body }),
  update: (id: string, body: Partial<OrganizationFields>) =>
    api<Organization>(`/organizations/${id}`, { method: "PUT", body }),
  history: (id: string, signal?: AbortSignal) =>
    api<OrganizationVersion[]>(`/organizations/${id}/history`, { signal }),
  remove: (id: string) =>
    api<void>(`/organizations/${id}`, { method: "DELETE" }),
  restore: (id: string) =>
    api<Organization>(`/organizations/${id}/restore`, { method: "POST" }),
};
