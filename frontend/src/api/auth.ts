import { api } from "./client";
import type { Token, User } from "./types";
export const authApi = {
  login: (username: string, password: string) =>
    api<Token>("/auth/login", { method: "POST", body: { username, password } }),
  me: () => api<User>("/auth/me"),
};
