import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { RouteGuard } from "./features/auth/RouteGuard";
import { useAuth } from "./features/auth/AuthProvider";
import { LoginPage } from "./features/auth/LoginPage";
import { homeFor, navigation } from "./features/auth/permissions";
import { lazy, Suspense } from "react";
import { AppLayout } from "./components/AppLayout";
import { Loading } from "./components/ui";
const CapacityPage = lazy(() =>
  import("./features/capacity/CapacityPage").then((m) => ({
    default: m.CapacityPage,
  })),
);
const QueuePage = lazy(() =>
  import("./features/queue/QueuePage").then((m) => ({ default: m.QueuePage })),
);
const BedsPage = lazy(() =>
  import("./features/beds/BedsPage").then((m) => ({ default: m.BedsPage })),
);
const PatientsPage = lazy(() =>
  import("./features/patients/PatientsPage").then((m) => ({
    default: m.PatientsPage,
  })),
);
const ReferralsPage = lazy(() =>
  import("./features/referrals/ReferralsPage").then((m) => ({
    default: m.ReferralsPage,
  })),
);
const AnalyticsPage = lazy(() =>
  import("./features/analytics/AnalyticsPage").then((m) => ({
    default: m.AnalyticsPage,
  })),
);
const AccountsPage = lazy(() =>
  import("./features/accounts/AccountsPage").then((m) => ({
    default: m.AccountsPage,
  })),
);
const PortalPage = lazy(() =>
  import("./features/portal/PortalPage").then((m) => ({
    default: m.PortalPage,
  })),
);
const BillingPage = lazy(() =>
  import("./features/billing/BillingPage").then((m) => ({
    default: m.BillingPage,
  })),
);
const OrganizationsPage = lazy(() =>
  import("./features/organizations/OrganizationsPage").then((m) => ({
    default: m.OrganizationsPage,
  })),
);
const screens = {
  "/organizations": OrganizationsPage,
  "/capacity": CapacityPage,
  "/queue": QueuePage,
  "/beds": BedsPage,
  "/patients": PatientsPage,
  "/referrals": ReferralsPage,
  "/analysis": AnalyticsPage,
  "/accounts": AccountsPage,
  "/portal": PortalPage,
  "/billing": BillingPage,
};

function Home() {
  const { user } = useAuth();
  return <Navigate to={user ? homeFor(user.role) : "/login"} replace />;
}
function LoginRoute() {
  const { user } = useAuth();
  return user ? <Home /> : <LoginPage />;
}
export function App() {
  return (
    <BrowserRouter>
      <a
        href="#main"
        className="sr-only z-[9999] rounded bg-surface p-3 text-accent-strong focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Saltar al contenido
      </a>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/login" element={<LoginRoute />} />
          <Route element={<AppLayout />}>
            {navigation.map((item) => {
              const Screen = screens[item.path as keyof typeof screens];
              return (
                <Route
                  key={item.path}
                  element={<RouteGuard path={item.path} />}
                >
                  <Route path={`${item.path}/*`} element={<Screen />} />
                </Route>
              );
            })}
          </Route>
          <Route path="*" element={<Home />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
