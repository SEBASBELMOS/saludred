import React from "react";
import ReactDOM from "react-dom/client";
import { PrimeReactProvider, addLocale } from "primereact/api";
import Tailwind from "primereact/passthrough/tailwind";
import "@fontsource/source-sans-3/latin-400.css";
import "@fontsource/source-sans-3/latin-600.css";
import "@fontsource/source-sans-3/latin-700.css";
import "@fontsource/barlow-semi-condensed/latin-600.css";
import "primeicons/primeicons.css";
import "./tailwind.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./api/query";
import { AuthProvider } from "./features/auth/AuthProvider";
import { ToastProvider } from "./components/ui";
import { App } from "./App";
addLocale("es", {
  accept: "Aceptar",
  reject: "Cancelar",
  emptyMessage: "No hay resultados",
  aria: {
    close: "Cerrar",
    navigation: "Navegación",
    previous: "Anterior",
    next: "Siguiente",
    pageLabel: "Página {page}",
  },
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <PrimeReactProvider value={{ unstyled: true, pt: Tailwind, locale: "es" }}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AuthProvider>
      </QueryClientProvider>
    </PrimeReactProvider>
  </React.StrictMode>,
);
