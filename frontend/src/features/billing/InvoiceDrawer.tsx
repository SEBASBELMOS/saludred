import type { Factura } from "../../api/billing";
import { cop, dateTime } from "../../components/format";
import { Drawer } from "../../components/Drawer";
import { Action } from "../../components/ui";
import { EstadoChip } from "./EstadoChip";
import { servicioNames, shortId } from "./labels";

// The invoice with its full trail: every state change is appended, never rewritten.
export function InvoiceDrawer({
  invoice,
  patientName,
  onClose,
  onPay,
  onVoid,
  onReceipt,
}: {
  invoice: Factura | null;
  patientName: string;
  onClose: () => void;
  onPay: (invoice: Factura) => void;
  onVoid: (invoice: Factura) => void;
  onReceipt: (invoice: Factura) => void;
}) {
  return (
    <Drawer
      visible={!!invoice}
      onClose={onClose}
      title={invoice ? servicioNames[invoice.servicio] : ""}
      label="Detalle de la factura"
      footer={
        invoice && (
          <>
            <Action
              secondary
              icon="pi pi-download"
              label="Recibo (PDF)"
              aria-label="Descargar recibo (PDF)"
              onClick={() => onReceipt(invoice)}
            />
            {invoice.estado_pago === "pendiente" && (
              <>
                <Action
                  secondary
                  danger
                  label="Anular"
                  onClick={() => onVoid(invoice)}
                />
                <Action label="Registrar pago" onClick={() => onPay(invoice)} />
              </>
            )}
          </>
        )
      }
    >
      {invoice && (
        <div className="space-y-6">
          <div className="flex items-baseline justify-between gap-3">
            <strong className="font-display text-2xl font-semibold tabular-nums">
              {cop(invoice.valor)}
            </strong>
            <EstadoChip estado={invoice.estado_pago} />
          </div>
          <dl className="divide-y divide-line border-y border-line text-sm">
            {[
              ["Descripción", invoice.descripcion],
              [
                "Cantidad",
                `${invoice.cantidad} × ${cop(invoice.valor_unitario)}`,
              ],
              ["Paciente", patientName],
              [
                "Origen",
                invoice.origen === "automatica" ? "Automática" : "Manual",
              ],
              ["Creada", dateTime(invoice.fecha)],
              [
                "Creada por",
                invoice.creado_por === "sistema"
                  ? "Sistema"
                  : shortId(invoice.creado_por),
              ],
              ...(invoice.reporte_id
                ? [["Reporte de IA", shortId(invoice.reporte_id)]]
                : []),
              ...(invoice.cama_id ? [["Cama", shortId(invoice.cama_id)]] : []),
              ...(invoice.remision_id
                ? [["Remisión", shortId(invoice.remision_id)]]
                : []),
            ].map(([term, value]) => (
              <div key={term} className="flex justify-between gap-4 py-2.5">
                <dt className="text-ink-muted">{term}</dt>
                <dd className="min-w-0 break-words text-right font-semibold">
                  {value}
                </dd>
              </div>
            ))}
          </dl>
          <section aria-labelledby="history-heading">
            <h3 id="history-heading" className="mb-3 text-lg font-semibold">
              Historial
            </h3>
            <ol className="divide-y divide-line border-y border-line">
              {invoice.historial.map((entry, index) => (
                <li key={index} className="py-2.5 text-sm">
                  <div className="flex items-baseline justify-between gap-3">
                    <EstadoChip estado={entry.estado} />
                    <span className="text-ink-muted">
                      {dateTime(entry.fecha)}
                    </span>
                  </div>
                  <p className="mt-1 break-words text-ink-muted">
                    {entry.usuario === "sistema"
                      ? "Sistema"
                      : shortId(entry.usuario)}
                    {entry.nota ? `: ${entry.nota}` : ""}
                  </p>
                </li>
              ))}
            </ol>
          </section>
        </div>
      )}
    </Drawer>
  );
}
