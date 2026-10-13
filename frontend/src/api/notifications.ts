import { api } from "./client";
import type { AppNotification } from "./types";
export const notificationsApi = {
  list: (signal?: AbortSignal) =>
    api<AppNotification[]>("/notifications?limit=100", { signal }),
  unread: (signal?: AbortSignal) =>
    api<AppNotification[]>("/notifications?leida=false&limit=100", { signal }),
  read: (id: string) =>
    api<AppNotification>(`/notifications/${id}/read`, { method: "POST" }),
};
