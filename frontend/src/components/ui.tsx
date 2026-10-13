import {
  Children,
  cloneElement,
  createContext,
  isValidElement,
  useContext,
  useId,
  useRef,
  useState,
  useEffect,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import { Button, type ButtonProps } from "primereact/button";
import { InputText, type InputTextProps } from "primereact/inputtext";
import {
  InputTextarea,
  type InputTextareaProps,
} from "primereact/inputtextarea";
import { Dialog } from "primereact/dialog";
import { Toast } from "primereact/toast";
import {
  interaction,
  MotionItems,
  motionDuration,
  overlayTransition,
  toastTransition,
  useReducedMotion,
} from "./motion";
export { interaction };
const DialogExit = createContext<((callback: () => void) => void) | null>(null);
export const focus =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
const inputClass = `w-full min-w-0 rounded-lg border border-line-strong bg-surface px-3 py-[5px] text-base text-ink disabled:bg-surface-sunken max-sm:py-[9px] ${focus}`;
// Quiet text-style button for secondary navigation inside rows ("Historial").
export const linkButton = `rounded font-semibold text-accent underline underline-offset-2 transition-colors duration-press ease-out hov:text-accent-strong ${focus}`;
// The focus ring of a button fades in with the same 160 ms as its hover: a
// transparent outline that takes its colour and offset on keyboard focus.
export const buttonFocus =
  "outline outline-2 outline-offset-0 outline-transparent focus-visible:outline-accent focus-visible:outline-offset-2";
export const buttonShape =
  "relative inline-flex max-w-full items-center justify-center gap-2 rounded-lg border px-4 min-h-9 text-sm font-semibold max-sm:min-h-11";
// The four looks of a button. Each one changes colour and border on hover, only
// where a real pointer exists (hov), so a touch screen never gets a stuck hover.
export const buttonLooks = {
  primary:
    "border-accent bg-accent text-white hov:border-accent-strong hov:bg-accent-strong",
  secondary:
    "border-line-strong bg-surface text-accent-strong hov:border-accent hov:bg-accent-tint",
  dangerPrimary:
    "border-critical bg-critical text-white hov:border-critical-ink hov:bg-critical-ink",
  dangerSecondary:
    "border-critical/50 bg-surface text-critical-ink hov:border-critical hov:bg-critical-tint",
};
export function Action({
  secondary,
  danger,
  className = "",
  loading,
  ...props
}: ButtonProps & { secondary?: boolean; danger?: boolean }) {
  const look = danger
    ? secondary
      ? buttonLooks.dangerSecondary
      : buttonLooks.dangerPrimary
    : secondary
      ? buttonLooks.secondary
      : buttonLooks.primary;
  const button = useRef<HTMLButtonElement | null>(null);
  const width = useRef<number>();
  const exit = useContext(DialogExit);
  useEffect(() => {
    if (!loading) width.current = button.current?.getBoundingClientRect().width;
  }, [loading, props.label]);
  return (
    <Button
      type="button"
      {...props}
      ref={(element) => {
        button.current = element as unknown as HTMLButtonElement | null;
      }}
      loading={loading}
      aria-busy={loading || undefined}
      style={{
        ...props.style,
        ...(loading && width.current ? { width: width.current } : {}),
      }}
      onClick={(event) => {
        if (
          exit &&
          props.onClick &&
          ["Cancelar", "Cerrar", "Volver"].includes(props.label || "")
        )
          exit(() => props.onClick?.(event));
        else props.onClick?.(event);
      }}
      pt={{
        root: {
          className: `${buttonShape} ${interaction} disabled:cursor-not-allowed disabled:opacity-50 ${buttonFocus} ${look} ${className}`,
        },
        label: { className: `font-semibold ${loading ? "opacity-0" : ""}` },
        loadingIcon: {
          className:
            "absolute left-1/2 top-1/2 -ml-2 -mt-2 h-4 w-4 motion-safe:animate-spin",
        },
        icon: { className: "text-sm" },
      }}
      ptOptions={{ mergeSections: false, mergeProps: false }}
    />
  );
}
export function Field({ label, ...props }: InputTextProps & { label: string }) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className="block min-w-0 space-y-1.5 text-sm font-semibold text-ink"
    >
      <span>{label}</span>
      <InputText
        {...props}
        id={id}
        className={inputClass}
        ptOptions={{ mergeSections: false, mergeProps: false }}
        pt={{ root: {} }}
      />
    </label>
  );
}
export function SelectField({
  label,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className="block min-w-0 space-y-1.5 text-sm font-semibold text-ink"
    >
      <span>{label}</span>
      <select {...props} id={id} className={inputClass}>
        {children}
      </select>
    </label>
  );
}
export function TextField({
  label,
  ...props
}: InputTextareaProps & { label: string }) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className="block min-w-0 space-y-1.5 text-sm font-semibold text-ink"
    >
      <span>{label}</span>
      <InputTextarea
        {...props}
        id={id}
        className={inputClass}
        pt={{ root: {} }}
        ptOptions={{ mergeSections: false, mergeProps: false }}
      />
    </label>
  );
}
export const options = (entries: Record<string, string>) =>
  Object.entries(entries).map(([value, label]) => (
    <option key={value} value={value}>
      {label}
    </option>
  ));
export function Panel({
  id,
  title,
  description,
  children,
  action,
}: {
  id?: string;
  title: string;
  description?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section
      id={id}
      className="min-w-0 scroll-mt-16 rounded-xl border border-line bg-surface"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold leading-tight">{title}</h2>
          {description && (
            <p className="mt-1 text-sm text-ink-muted">{description}</p>
          )}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}
// Right-aligns the cells of numeric columns so figures line up.
function alignCells(children: ReactNode, numeric: number[], headers: string[]) {
  return Children.map(children, (row) =>
    isValidElement<{ children?: ReactNode }>(row) && row.type === "tr"
      ? cloneElement(
          row,
          {},
          Children.map(row.props.children, (cell, index) =>
            isValidElement<{ className?: string }>(cell)
              ? cloneElement(cell as ReactElement<Record<string, unknown>>, {
                  "data-label": headers[index],
                  ...(numeric.includes(index)
                    ? {
                        className:
                          `${cell.props.className ?? ""} text-right`.trim(),
                      }
                    : {}),
                })
              : cell,
          ),
        )
      : row,
  );
}
// A titled group without a box: grouping comes from space and hairlines.
export function Section({
  title,
  description,
  action,
  children,
}: {
  title: ReactNode;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold leading-tight">{title}</h2>
          {description && (
            <p className="mt-0.5 text-sm text-ink-muted">{description}</p>
          )}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
export function DataTable({
  headers,
  children,
  caption,
  numeric = [],
  framed = false,
  stack = false,
  sticky = false,
}: {
  headers: string[];
  children: ReactNode;
  caption: string;
  /** Indexes of columns that hold figures; their header is right aligned. */
  numeric?: number[];
  framed?: boolean;
  /** Below 640 px each row becomes a stacked list item instead of a clipped table. */
  stack?: boolean;
  /** Keeps the header in view while a long body scrolls inside the table. */
  sticky?: boolean;
}) {
  return (
    <div
      className={`relative max-w-full overflow-x-auto ${sticky ? "max-h-[70dvh] overflow-y-auto" : ""} ${framed ? "rounded-xl border border-line bg-surface" : "rounded-b-xl"} ${focus}`}
      tabIndex={0}
      role="region"
      aria-label={caption}
    >
      <table
        className={`data-table w-full border-collapse text-left text-sm ${stack ? "data-table-stack" : ""}`}
      >
        <caption className="sr-only">{caption}</caption>
        <thead
          className={`bg-surface-sunken text-xs font-semibold text-ink-muted ${sticky ? "sticky top-0 z-10" : ""}`}
        >
          <tr>
            {headers.map((h, index) => (
              <th
                scope="col"
                className={`px-4 py-2.5 ${numeric.includes(index) ? "text-right" : ""}`}
                key={h}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          <MotionItems>{alignCells(children, numeric, headers)}</MotionItems>
        </tbody>
      </table>
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const [visible, setVisible] = useState(true);
  const afterExit = useRef<(() => void) | null>(null);
  const reduced = useReducedMotion();
  const closeWith = (callback: () => void) => {
    if (reduced) {
      callback();
      return;
    }
    afterExit.current = callback;
    setVisible(false);
  };
  return (
    <Dialog
      visible={visible}
      transitionOptions={{
        classNames: overlayTransition,
        timeout: reduced
          ? 0
          : { enter: motionDuration("base"), exit: motionDuration("exit") },
        onExited: () => afterExit.current?.(),
      }}
      header={title}
      onHide={() => closeWith(onClose)}
      modal
      draggable={false}
      resizable={false}
      blockScroll
      closeOnEscape
      focusOnShow
      className={
        wide
          ? "w-[min(960px,calc(100vw-2rem))]"
          : "w-[min(640px,calc(100vw-2rem))]"
      }
      pt={{
        root: {
          className:
            "flex max-h-[90dvh] flex-col overflow-hidden rounded-xl bg-surface shadow-overlay",
        },
        mask: {
          className:
            "fixed inset-0 flex items-center justify-center bg-ink/40 p-4",
        },
        header: {
          className:
            "flex shrink-0 items-center justify-between gap-4 border-b border-line bg-surface p-5",
        },
        headerTitle: {
          className: "text-xl font-semibold text-ink",
        },
        headerIcons: { className: "flex shrink-0" },
        closeButton: {
          className: `grid h-10 w-10 place-items-center rounded-lg text-ink-muted hov:bg-surface-sunken ${interaction} ${focus}`,
          "aria-label": "Cerrar ventana",
        },
        content: {
          className: "min-h-0 overflow-y-auto bg-surface p-5 text-ink",
        },
      }}
      ptOptions={{ mergeSections: false, mergeProps: false }}
    >
      <DialogExit.Provider value={closeWith}>{children}</DialogExit.Provider>
    </Dialog>
  );
}
export function FormActions({
  label,
  pending,
  disabled,
  danger,
  onClose,
}: {
  label: string;
  pending?: boolean;
  disabled?: boolean;
  danger?: boolean;
  onClose: () => void;
}) {
  return (
    <div className="mt-6 flex flex-wrap justify-end gap-3 border-t border-line pt-4">
      <Action secondary label="Cancelar" onClick={onClose} disabled={pending} />
      <Action
        type="submit"
        danger={danger}
        label={label}
        loading={pending}
        disabled={pending || disabled}
      />
    </div>
  );
}
export function Notice({
  children,
  amber = false,
}: {
  children: ReactNode;
  amber?: boolean;
}) {
  return (
    <div
      role="alert"
      className={`my-3 rounded-lg border p-3 text-sm ${amber ? "border-bed-cleaning/40 bg-bed-cleaning-tint text-bed-cleaning-ink" : "border-critical/30 bg-critical-tint text-critical-ink"}`}
    >
      {children}
    </div>
  );
}
export function Loading({
  variant = "table",
}: {
  variant?: "table" | "cards";
}) {
  return (
    <div
      role="status"
      aria-label="Cargando información"
      aria-busy="true"
      className="min-w-0 p-5"
    >
      <span className="sr-only">Cargando información…</span>
      <div aria-hidden="true" className="space-y-4">
        {variant === "cards" && (
          <div className="h-10 rounded-lg bg-surface-sunken motion-safe:animate-pulse" />
        )}
        {Array.from({ length: variant === "cards" ? 3 : 5 }, (_, index) => (
          <div
            key={index}
            className={`rounded-lg bg-surface-sunken motion-safe:animate-pulse ${variant === "cards" ? "h-24" : "flex h-10 items-center gap-4 px-3"}`}
          >
            {variant !== "cards" && (
              <>
                <span className="block h-3 w-1/3 rounded bg-line" />
                <span className="block h-3 w-1/4 rounded bg-line" />
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
export interface ToastAction {
  label: string;
  onClick: () => Promise<void>;
}
function ToastActionButton({ action }: { action: ToastAction }) {
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const busy = useRef(false);
  return (
    <div className="mt-3">
      <Action
        secondary
        label={done ? "Restaurada" : action.label}
        loading={pending}
        disabled={pending || done}
        onClick={async () => {
          if (busy.current) return;
          busy.current = true;
          setPending(true);
          setError("");
          try {
            await action.onClick();
            setDone(true);
          } catch (failure) {
            setError(
              failure instanceof Error
                ? failure.message
                : "No se pudo completar la operación.",
            );
            busy.current = false;
          } finally {
            setPending(false);
          }
        }}
      />
      {error && <Notice>{error}</Notice>}
    </div>
  );
}
const ToastContext = createContext<
  (
    message: string,
    severity?: "success" | "warn" | "error" | "info",
    action?: ToastAction,
  ) => void
>(() => {});
export const useToast = () => useContext(ToastContext);
export function ToastProvider({ children }: { children: ReactNode }) {
  const ref = useRef<Toast>(null);
  const reduced = useReducedMotion();
  return (
    <ToastContext.Provider
      value={(detail, severity = "success", action) =>
        ref.current?.show({
          severity,
          detail,
          life: motionDuration("toast") || 7000,
          content: (
            <div className="min-w-0 flex-1 py-1 pr-2">
              <div className="flex items-start gap-3">
                <i
                  aria-hidden="true"
                  className={`pi mt-1 ${severity === "success" ? "pi-check-circle text-bed-available-ink" : severity === "info" ? "pi-bell text-accent-strong" : severity === "warn" ? "pi-exclamation-triangle text-bed-cleaning-ink" : "pi-times-circle text-critical"}`}
                />
                <div>
                  <strong className="block text-sm">
                    {severity === "success"
                      ? "Listo"
                      : severity === "info"
                        ? "Nueva notificación"
                        : severity === "warn"
                          ? "Revisa este aviso"
                          : "No se pudo completar"}
                  </strong>
                  <p className="mt-1 break-words text-sm text-ink-muted">
                    {detail}
                  </p>
                  {action && <ToastActionButton action={action} />}
                </div>
              </div>
            </div>
          ),
        })
      }
    >
      {children}
      <Toast
        ref={ref}
        position="top-right"
        transitionOptions={{
          classNames: toastTransition,
          timeout: {
            enter: reduced ? 0 : motionDuration("base"),
            exit: reduced ? 0 : motionDuration("exit"),
          },
        }}
        pt={{
          root: {
            className:
              "fixed right-4 top-4 z-[1200] w-96 max-w-[calc(100vw-2rem)] max-sm:!bottom-4 max-sm:!left-4 max-sm:!right-4 max-sm:!top-auto max-sm:w-auto max-sm:max-w-none",
          },
          message: {
            className:
              "group relative mb-3 overflow-hidden rounded-xl bg-surface shadow-overlay",
          },
          content: { className: "flex items-start gap-2 p-4" },
          closeButton: {
            className: `grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-muted hov:bg-surface-sunken ${interaction} ${focus}`,
            "aria-label": "Cerrar notificación",
          },
        }}
        ptOptions={{ mergeSections: false, mergeProps: false }}
        className="max-w-[calc(100vw-2rem)]"
      />
    </ToastContext.Provider>
  );
}
