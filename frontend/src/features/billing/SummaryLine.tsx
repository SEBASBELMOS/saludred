import type { Resumen } from "../../api/billing";
import { cop } from "../../components/format";
import { estadoPlural, servicioNames } from "./labels";
import type { EstadoPago, Servicio } from "../../api/billing";

const estados: EstadoPago[] = ["pendiente", "pagada", "anulada"];
const servicios = Object.keys(servicioNames) as Servicio[];

// One quiet line for the totals, in the same style as the bed status strip.
export function SummaryLine({ summary }: { summary: Resumen }) {
  const byServicio = servicios.filter(
    (servicio) => summary.por_servicio[servicio]?.cantidad,
  );
  return (
    <div
      aria-label="Resumen de facturación"
      role="group"
      className="flex flex-wrap items-baseline gap-x-8 gap-y-3 border-b border-line pb-5"
    >
      <div className="flex items-baseline gap-2">
        <strong
          data-testid="total-facturado"
          className="font-display text-2xl font-semibold tabular-nums"
        >
          {cop(summary.total_facturado.valor)}
        </strong>
        <span className="text-sm text-ink-muted">
          facturado en {summary.total_facturado.cantidad}{" "}
          {summary.total_facturado.cantidad === 1 ? "factura" : "facturas"}
        </span>
      </div>
      <ul
        aria-label="Por estado de pago"
        className="flex flex-wrap gap-x-5 gap-y-1 text-sm"
      >
        {estados.map((estado) => {
          const total = summary.por_estado[estado];
          if (!total?.cantidad) return null;
          return (
            <li key={estado}>
              <strong className="font-semibold tabular-nums">
                {total.cantidad}
              </strong>{" "}
              <span className="text-ink-muted">{estadoPlural[estado]}</span>{" "}
              <span className="tabular-nums">{cop(total.valor)}</span>
            </li>
          );
        })}
      </ul>
      <ul
        aria-label="Por servicio"
        className="flex flex-wrap gap-x-5 gap-y-1 text-sm"
      >
        {byServicio.map((servicio) => (
          <li key={servicio}>
            <span className="text-ink-muted">{servicioNames[servicio]}</span>{" "}
            <strong className="font-semibold tabular-nums">
              {summary.por_servicio[servicio].cantidad}
            </strong>{" "}
            <span className="tabular-nums">
              {cop(summary.por_servicio[servicio].valor)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
