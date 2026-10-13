import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PrimeReactProvider } from "primereact/api";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
export function renderUi(children: ReactNode, route = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <PrimeReactProvider value={{ unstyled: true, cssTransition: false }}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
      </QueryClientProvider>
    </PrimeReactProvider>
  );
  const rendered = render(children, { wrapper: Wrapper });
  return { ...rendered, client };
}
