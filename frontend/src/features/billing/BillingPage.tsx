import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import {
  billingApi,
  type Conciliacion,
  type Factura,
  type FacturaFilters,
} from "../../api/billing";
import { patientsApi } from "../../api/patients";
import { cop, dateTime, fullName } from "../../components/format";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import {
  Action,
  DataTable,
  Field,
  Loading,
  Notice,
  SelectField,
  linkButton,
  useToast,
} from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canResolvePatients } from "../auth/permissions";
import { EstadoChip } from "./EstadoChip";
import { InvoiceDrawer } from "./InvoiceDrawer";
import { NewInvoiceDialog, PayDialog, VoidDialog } from "./InvoiceDialogs";
import { SummaryLine } from "./SummaryLine";
import {
  BILLING_UNAVAILABLE,
  estadoNames,
  isUnavailable,
  servicioNames,
  shortId,
} from "./labels";
import {
  INVOICES_KEY,
  SUMMARY_KEY,
  useRefreshBilling,
} from "./useBillingFailure";

const FLASH_MS = 1200;

// Rows that appear after the first load get a brief background flash: the only motion here.
function useNewRows(rows: Factura[] | undefined, scope: string) {
  const seen = useRef<{ scope: string; ids: Set<string> } | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!rows) return;
    if (!seen.current || seen.current.scope !== scope) {
      seen.current = { scope, ids: new Set(rows.map((row) => row.id)) };
      return;
    }
    const added = rows.filter((row) => !seen.current!.ids.has(row.id));
    rows.forEach((row) => seen.current!.ids.add(row.id));
    if (!added.length) return;
    setFresh((current) => new Set([...current, ...added.map((row) => row.id)]));
    const timer = setTimeout(
      () =>
        setFresh((current) => {
          const next = new Set(current);
          added.forEach((row) => next.delete(row.id));
          return next;
        }),
      FLASH_MS,
    );
    return () => clearTimeout(timer);
  }, [rows, scope]);
  return fresh;
}

function Reference({ invoice, staff }: { invoice: Factura; staff: boolean }) {
  const item = invoice.reporte_id
    ? { label: "Reporte de IA", id: invoice.reporte_id, to: "/patients" }
    : invoice.cama_id
      ? { label: "Cama", id: invoice.cama_id, to: "/beds" }
      : invoice.remision_id
        ? { label: "Remisión", id: invoice.remision_id, to: "" }
        : null;
  if (!item) return null;
  const text = `${item.label} ${shortId(item.id)}`;
  // Only roles that already read clinical data get a link to it.
  return staff && item.to ? (
    <Link to={item.to} className={linkButton} title={item.id}>
      {text}
    </Link>
  ) : (
    <span title={item.id}>{text}</span>
  );
}

function ReconcileResult({ result }: { result: Conciliacion }) {
  return (
    <p role="status" className="text-sm text-ink-muted">
      Conciliación:{" "}
      <strong className="font-semibold text-ink">
        {result.total_creadas}{" "}
        {result.total_creadas === 1 ? "factura creada" : "facturas creadas"}
      </strong>
      . Análisis de IA: {result.analisis_ia.revisados} revisados,{" "}
      {result.analisis_ia.creadas} creadas, {result.analisis_ia.ya_facturados}{" "}
      ya facturados. Días cama: {result.dia_cama.revisados} revisados,{" "}
      {result.dia_cama.creadas} creadas, {result.dia_cama.ya_facturados} ya
      facturados.
    </p>
  );
}

export function BillingPage() {
  const { user } = useAuth();
  const toast = useToast();
  const refresh = useRefreshBilling();
  const staff = !!user && canResolvePatients(user.role);
  const [filters, setFilters] = useState<FacturaFilters>({});
  const [open, setOpen] = useState<string | null>(null);
  const [dialog, setDialog] = useState<
    { kind: "new" } | { kind: "pay" | "void"; invoice: Factura } | null
  >(null);
  const [reconciled, setReconciled] = useState<Conciliacion | null>(null);
  const scope = JSON.stringify(filters);

  const invoices = useQuery({
    queryKey: [...INVOICES_KEY, filters],
    queryFn: ({ signal }) => billingApi.list(filters, signal),
  });
  const summary = useQuery({
    queryKey: SUMMARY_KEY,
    queryFn: ({ signal }) => billingApi.summary(signal),
  });
  const fresh = useNewRows(invoices.data, scope);

  // Names come from the patient record, which only the admin may read.
  const ids = useMemo(
    () => [...new Set((invoices.data ?? []).map((row) => row.paciente_id))],
    [invoices.data],
  );
  const names = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["billing-patient", id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        patientsApi.get(id, signal),
      enabled: staff,
      staleTime: 5 * 60_000,
      retry: false,
      meta: { silent: true },
    })),
  });
  const nameOf = (id: string) => {
    const found = names[ids.indexOf(id)]?.data;
    return found ? fullName(found) : `Paciente ${shortId(id)}`;
  };

  const reconcile = useMutation({
    mutationFn: () => billingApi.reconcile(),
    onSuccess: async (result) => {
      setReconciled(result);
      toast(
        result.total_creadas
          ? `Conciliación: ${result.total_creadas} facturas creadas`
          : "Conciliación: no faltaba ninguna factura",
      );
      await refresh();
    },
    onError: (error) =>
      toast(
        isUnavailable(error) ? BILLING_UNAVAILABLE : error.message,
        "error",
      ),
  });
  const receipt = async (invoice: Factura) => {
    try {
      await billingApi.downloadReceipt(invoice.id);
    } catch (error) {
      toast(
        error instanceof Error && isUnavailable(error)
          ? BILLING_UNAVAILABLE
          : `No se pudo descargar el recibo. ${error instanceof Error ? error.message : ""}`,
        "error",
      );
    }
  };

  const rows = invoices.data ?? [];
  const selected = rows.find((row) => row.id === open) ?? null;
  const down = isUnavailable(invoices.error) || isUnavailable(summary.error);
  const set = (key: keyof FacturaFilters) => (value: string) =>
    setFilters((current) => ({ ...current, [key]: value || undefined }));

  return (
    <div className="space-y-6">
      {down && <Notice amber>{BILLING_UNAVAILABLE}</Notice>}
      {summary.data && <SummaryLine summary={summary.data} />}
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <div className="w-40 max-sm:w-[calc(50%-0.5rem)]">
          <SelectField
            label="Estado de pago"
            value={filters.estado_pago ?? ""}
            onChange={(event) => set("estado_pago")(event.target.value)}
          >
            <option value="">Todos</option>
            {Object.entries(estadoNames).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </SelectField>
        </div>
        <div className="w-44 max-sm:w-[calc(50%-0.5rem)]">
          <SelectField
            label="Servicio"
            value={filters.servicio ?? ""}
            onChange={(event) => set("servicio")(event.target.value)}
          >
            <option value="">Todos</option>
            {Object.entries(servicioNames).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </SelectField>
        </div>
        <div className="w-40 max-sm:w-[calc(50%-0.5rem)]">
          <Field
            label="Desde"
            type="date"
            value={filters.desde ?? ""}
            max={filters.hasta}
            onChange={(event) => set("desde")(event.target.value)}
          />
        </div>
        <div className="w-40 max-sm:w-[calc(50%-0.5rem)]">
          <Field
            label="Hasta"
            type="date"
            value={filters.hasta ?? ""}
            min={filters.desde}
            onChange={(event) => set("hasta")(event.target.value)}
          />
        </div>
        <div className="ml-auto flex flex-wrap gap-3 max-sm:ml-0 max-sm:w-full">
          <Action
            secondary
            icon="pi pi-sync"
            label="Conciliar"
            loading={reconcile.isPending}
            disabled={reconcile.isPending}
            onClick={() => reconcile.mutate()}
          />
          <Action
            icon="pi pi-plus"
            label="Nueva factura"
            onClick={() => setDialog({ kind: "new" })}
          />
        </div>
      </div>
      {reconciled && <ReconcileResult result={reconciled} />}
      {invoices.isPending ? (
        <Loading />
      ) : invoices.error && !down ? (
        <ErrorState error={invoices.error} retry={invoices.refetch} />
      ) : down ? null : rows.length === 0 ? (
        <EmptyState title="Sin facturas para este filtro">
          Las facturas automáticas aparecen cuando un clínico aprueba un reporte
          de IA o cuando se libera una cama. También puedes crear una manual.
        </EmptyState>
      ) : (
        <DataTable
          framed
          stack
          sticky
          caption="Facturas"
          numeric={[4, 5]}
          headers={[
            "Fecha",
            "Paciente",
            "Servicio",
            "Descripción",
            "Cantidad",
            "Valor",
            "Estado de pago",
            "Origen",
            "Acciones",
          ]}
        >
          {rows.map((invoice) => (
            <tr
              key={invoice.id}
              data-testid="invoice-row"
              className={`cursor-pointer ${fresh.has(invoice.id) ? "animate-flash" : ""}`}
              onClick={() => setOpen(invoice.id)}
            >
              <td>{dateTime(invoice.fecha)}</td>
              <td title={invoice.paciente_id}>{nameOf(invoice.paciente_id)}</td>
              <td>{servicioNames[invoice.servicio]}</td>
              <td className="max-w-56 truncate max-sm:max-w-none max-sm:whitespace-normal">
                <button
                  type="button"
                  className={`${linkButton} max-w-full truncate text-left no-underline max-sm:min-h-11 max-sm:whitespace-normal`}
                  aria-label={`Ver historial: ${invoice.descripcion}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    setOpen(invoice.id);
                  }}
                >
                  {invoice.descripcion}
                </button>
              </td>
              <td className="tabular-nums">{invoice.cantidad}</td>
              <td className="font-semibold tabular-nums">
                {cop(invoice.valor)}
              </td>
              <td>
                <EstadoChip estado={invoice.estado_pago} />
              </td>
              <td onClick={(event) => event.stopPropagation()}>
                <span>
                  {invoice.origen === "automatica" ? "Automática" : "Manual"}
                </span>
                <span className="block text-xs text-ink-muted">
                  <Reference invoice={invoice} staff={staff} />
                </span>
              </td>
              <td onClick={(event) => event.stopPropagation()}>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {invoice.estado_pago === "pendiente" && (
                    <>
                      <button
                        type="button"
                        className={`${linkButton} max-sm:min-h-11`}
                        onClick={() => setDialog({ kind: "pay", invoice })}
                      >
                        Registrar pago
                      </button>
                      <button
                        type="button"
                        className={`${linkButton} max-sm:min-h-11`}
                        onClick={() => setDialog({ kind: "void", invoice })}
                      >
                        Anular
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    className={`${linkButton} max-sm:min-h-11`}
                    aria-label={`Descargar recibo (PDF): ${invoice.descripcion}`}
                    onClick={() => void receipt(invoice)}
                  >
                    Recibo (PDF)
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </DataTable>
      )}
      <InvoiceDrawer
        invoice={selected}
        patientName={selected ? nameOf(selected.paciente_id) : ""}
        onClose={() => setOpen(null)}
        onPay={(invoice) => setDialog({ kind: "pay", invoice })}
        onVoid={(invoice) => setDialog({ kind: "void", invoice })}
        onReceipt={(invoice) => void receipt(invoice)}
      />
      {dialog?.kind === "new" && (
        <NewInvoiceDialog onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === "pay" && (
        <PayDialog invoice={dialog.invoice} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === "void" && (
        <VoidDialog invoice={dialog.invoice} onClose={() => setDialog(null)} />
      )}
    </div>
  );
}
