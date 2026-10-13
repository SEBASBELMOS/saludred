import type { EstadoPago } from "../../api/billing";
import { estadoClasses, estadoNames, estadoSymbols } from "./labels";

export function EstadoChip({ estado }: { estado: EstadoPago }) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${estadoClasses[estado]}`}
    >
      <span aria-hidden="true">{estadoSymbols[estado]}</span>
      {estadoNames[estado]}
    </span>
  );
}
