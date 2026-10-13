import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { aiApi } from "../../api/ai";
import { Action, Loading } from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { variableLabel, variableValue } from "./words";
/**
 * The exact JSON the model received, in plain words. The raw JSON is one click away
 * so the clinician can check that nothing identifying left the system.
 */
export function PayloadView({ reportId }: { reportId: string }) {
  const [raw, setRaw] = useState(false);
  const query = useQuery({
    queryKey: ["ai-payload", reportId],
    queryFn: ({ signal }) => aiApi.payload(reportId, signal),
    // The payload never changes after the report exists.
    staleTime: Infinity,
  });
  if (query.error)
    return <ErrorState error={query.error} retry={query.refetch} />;
  if (!query.data) return <Loading />;
  const entries = Object.entries(query.data.payload);
  return (
    <section
      aria-label="Datos enviados al modelo"
      className="rounded-lg bg-surface-sunken p-4"
    >
      <h4 className="font-semibold">Datos enviados al modelo</h4>
      <p className="mt-1 text-sm text-ink-muted">
        Esto es todo lo que salió hacia el modelo. No incluye nombre, documento,
        contacto ni fecha de nacimiento: el seudónimo reemplaza la identidad.
      </p>
      <dl className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {entries.map(([key, value]) => (
          <div key={key} className="min-w-0">
            <dt className="text-xs text-ink-muted">
              {variableLabel(key)}
              <span className="ml-1">({key})</span>
            </dt>
            <dd className="mt-0.5 break-words text-sm font-semibold tabular-nums">
              {key === "pseudonym" ? String(value) : variableValue(key, value)}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-4">
        <Action
          secondary
          label={raw ? "Ocultar JSON" : "Ver JSON sin formato"}
          aria-expanded={raw}
          onClick={() => setRaw((open) => !open)}
        />
      </div>
      {raw && (
        <pre
          aria-label="JSON enviado al modelo"
          className="mt-3 overflow-x-auto whitespace-pre-wrap break-all rounded-lg border border-line bg-surface p-3 font-sans text-sm tabular-nums"
        >
          {JSON.stringify(query.data.payload, null, 2)}
        </pre>
      )}
    </section>
  );
}
