import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { authApi } from "../../api/auth";
import { onSessionExpired, sessionToken } from "../../api/client";
import type { User } from "../../api/types";
import { ErrorState } from "../../components/ErrorState";
import { Loading } from "../../components/ui";
interface AuthContextValue {
  user: User | null;
  message: string;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}
const AuthContext = createContext<AuthContextValue | null>(null);
export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [token, setToken] = useState(sessionToken.get);
  const [message, setMessage] = useState("");
  const query = useQuery({
    queryKey: ["session", token],
    queryFn: authApi.me,
    enabled: !!token,
    retry: false,
    staleTime: Infinity,
  });
  useEffect(
    () =>
      onSessionExpired(() => {
        setToken(null);
        setMessage("Tu sesión terminó. Vuelve a iniciar sesión.");
        client.clear();
      }),
    [client],
  );
  const logout = () => {
    sessionToken.clear();
    setToken(null);
    setMessage("");
    client.clear();
  };
  const login = async (username: string, password: string) => {
    const response = await authApi.login(username, password);
    sessionToken.set(response.access_token);
    const user = await authApi.me();
    client.clear();
    client.setQueryData(["session", response.access_token], user);
    setToken(response.access_token);
    setMessage("");
  };
  if (token && query.isPending) return <Loading />;
  if (token && query.error)
    return <ErrorState error={query.error} retry={query.refetch} />;
  return (
    <AuthContext.Provider
      value={{
        user: token ? (query.data ?? null) : null,
        message,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider is missing");
  return value;
}
