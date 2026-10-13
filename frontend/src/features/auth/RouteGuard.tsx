import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthProvider";
import { homeFor, menuFor } from "./permissions";
export function RouteGuard({ path }: { path: string }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  return menuFor(user.role).some((item) => item.path === path) ? (
    <Outlet />
  ) : (
    <Navigate to={homeFor(user.role)} replace />
  );
}
