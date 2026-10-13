import { useEffect, useId, useRef, useState } from "react";
import { dateOnly } from "../../components/format";
import { DataTable } from "../../components/ui";

export interface Day {
  date: string;
  admissions: number;
  discharges: number;
}
const height = 240;
const margin = { top: 12, right: 76, bottom: 40, left: 44 };
const dayMonth = (date: string) =>
  dateOnly(date).split(" ").slice(0, 2).join(" ");
/** A round axis maximum and its step, so the grid lines fall on whole numbers. */
export function niceScale(max: number, ticks = 4) {
  const rough = Math.max(max, ticks) / ticks;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= rough)!;
  return { step, top: Math.ceil(Math.max(max, 1) / step) * step };
}
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(280, Math.round(entry.contentRect.width))),
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}
const series = [
  {
    key: "admissions" as const,
    label: "Ingresos",
    line: "stroke-bed-occupied",
    dash: undefined,
  },
  {
    key: "discharges" as const,
    label: "Altas",
    line: "stroke-bed-cleaning",
    dash: "6 4",
  },
];
// Two lines, one axis, tokens only. Hover shows a crosshair with both values;
// the table under the chart carries the same numbers for anyone who prefers them.
export function OccupancyChart({ days }: { days: Day[] }) {
  const { ref, width } = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const { step, top } = niceScale(
    Math.max(...days.map((d) => Math.max(d.admissions, d.discharges))),
  );
  const x = (i: number) =>
    margin.left +
    (days.length > 1 ? (i / (days.length - 1)) * plotW : plotW / 2);
  const y = (v: number) => margin.top + plotH - (v / top) * plotH;
  const ticks = Array.from({ length: top / step + 1 }, (_, i) => i * step);
  const every = Math.max(
    1,
    Math.ceil(64 / (days.length > 1 ? plotW / (days.length - 1) : plotW)),
  );
  const total = (key: "admissions" | "discharges") =>
    days.reduce((sum, d) => sum + d[key], 0);
  const move = (clientX: number, left: number) => {
    const index = Math.round(
      ((clientX - left - margin.left) / plotW) * (days.length - 1),
    );
    setHover(Math.min(days.length - 1, Math.max(0, index)));
  };
  // End-of-line labels sit beside their last point, pushed apart when they would touch.
  const lastY = series.map((s) => y(days[days.length - 1][s.key]));
  const gap = Math.abs(lastY[0] - lastY[1]);
  const nudge = (index: number) =>
    gap >= 14
      ? 0
      : (lastY[index] <= lastY[1 - index] ? -1 : 1) * ((16 - gap) / 2);
  const active = hover === null ? null : days[hover];
  return (
    <div className="p-5">
      <ul className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-sm">
        {series.map((s) => (
          <li key={s.key} className="flex items-center gap-2">
            <svg width="22" height="8" aria-hidden="true">
              <line
                x1="1"
                x2="21"
                y1="4"
                y2="4"
                strokeWidth="2"
                strokeLinecap="round"
                strokeDasharray={s.dash}
                className={s.line}
              />
            </svg>
            <span>{s.label}</span>
            <span className="text-ink-muted">{total(s.key)} en total</span>
          </li>
        ))}
      </ul>
      <div ref={ref} className="relative">
        <svg
          width={width}
          height={height}
          role="img"
          aria-labelledby={titleId}
          className="block max-w-full touch-pan-y"
          onPointerMove={(e) =>
            move(e.clientX, e.currentTarget.getBoundingClientRect().left)
          }
          onPointerLeave={() => setHover(null)}
        >
          <title id={titleId}>
            {`Ingresos y altas por día, del ${dayMonth(days[0].date)} al ${dayMonth(days[days.length - 1].date)}. ${total("admissions")} ingresos y ${total("discharges")} altas. Los mismos datos están en la tabla de abajo.`}
          </title>
          <text x={margin.left} y={10} className="fill-ink-muted text-xs">
            Pacientes por día
          </text>
          {ticks.map((t) => (
            <g key={t}>
              <line
                x1={margin.left}
                x2={margin.left + plotW}
                y1={y(t)}
                y2={y(t)}
                className="stroke-line"
              />
              <text
                x={margin.left - 8}
                y={y(t)}
                dy="0.32em"
                textAnchor="end"
                className="fill-ink-muted text-xs tabular-nums"
              >
                {t}
              </text>
            </g>
          ))}
          {days.map((d, i) =>
            i % every === 0 || i === days.length - 1 ? (
              <text
                key={d.date}
                x={x(i)}
                y={margin.top + plotH + 18}
                textAnchor="middle"
                className="fill-ink-muted text-xs tabular-nums"
              >
                {dayMonth(d.date)}
              </text>
            ) : null,
          )}
          <text
            x={margin.left + plotW / 2}
            y={height - 4}
            textAnchor="middle"
            className="fill-ink-muted text-xs"
          >
            Día
          </text>
          {hover !== null && (
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={margin.top}
              y2={margin.top + plotH}
              className="stroke-line-strong"
            />
          )}
          {series.map((s, index) => (
            <g key={s.key}>
              <polyline
                fill="none"
                strokeWidth="2"
                strokeLinejoin="round"
                strokeLinecap="round"
                strokeDasharray={s.dash}
                className={s.line}
                points={days.map((d, i) => `${x(i)},${y(d[s.key])}`).join(" ")}
              />
              {hover !== null && (
                <circle
                  cx={x(hover)}
                  cy={y(days[hover][s.key])}
                  r="4.5"
                  strokeWidth="2"
                  className={`fill-surface ${s.line}`}
                />
              )}
              <text
                x={x(days.length - 1) + 10}
                y={lastY[index] + nudge(index)}
                dy="0.32em"
                className="fill-ink text-xs font-semibold"
              >
                {s.label}
              </text>
            </g>
          ))}
        </svg>
        {active && hover !== null && (
          <div
            role="presentation"
            className="pointer-events-none absolute top-2 z-10 min-w-36 rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-overlay"
            style={{
              left: Math.min(Math.max(x(hover) + 12, 0), width - 160),
            }}
          >
            <strong className="block">{dateOnly(active.date)}</strong>
            <span className="block tabular-nums">
              Ingresos: {active.admissions}
            </span>
            <span className="block tabular-nums">
              Altas: {active.discharges}
            </span>
          </div>
        )}
      </div>
      <details className="mt-4">
        <summary
          className={`inline-block cursor-pointer rounded text-sm font-semibold text-accent-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent`}
        >
          Ver los datos en tabla
        </summary>
        <div className="mt-3">
          <DataTable
            framed
            numeric={[1, 2, 3]}
            caption="Ingresos y altas por día"
            headers={["Fecha", "Ingresos", "Altas", "Diferencia"]}
          >
            {days.map((row) => (
              <tr key={row.date}>
                <td>{dateOnly(row.date)}</td>
                <td>{row.admissions}</td>
                <td>{row.discharges}</td>
                <td>
                  {row.admissions - row.discharges > 0 ? "+" : ""}
                  {row.admissions - row.discharges}
                </td>
              </tr>
            ))}
          </DataTable>
        </div>
      </details>
    </div>
  );
}
