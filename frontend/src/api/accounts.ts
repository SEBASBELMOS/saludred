import { api } from "./client";
import type { Account, AccountCreate, AuditLog, Page } from "./types";
export const accountsApi = {
  list: (locked: boolean, signal?: AbortSignal) =>
    api<Page<Account>>(
      `/admin/users?page_size=100${locked ? "&only_locked=true" : ""}`,
      { signal },
    ),
  change: (id: string, action: "lock" | "unlock", reason: string) =>
    api<Account>(`/admin/users/${id}/${action}`, {
      method: "POST",
      body: { reason },
    }),
  create: (body: AccountCreate) =>
    api<Account>("/admin/users", { method: "POST", body }),
  audit: (signal?: AbortSignal) =>
    api<Page<AuditLog>>("/audit-logs?page_size=20", { signal }),
};
