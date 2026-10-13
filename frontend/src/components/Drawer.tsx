import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { Sidebar } from "primereact/sidebar";
import { focus, interaction } from "./ui";
import { drawerTimeout, drawerTransition, useReducedMotion } from "./motion";

const focusable =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
// Keeps Tab inside the panel: from the last control it wraps to the first and back.
export function trapTab(event: KeyboardEvent<HTMLElement>) {
  if (event.key !== "Tab") return;
  const root = event.currentTarget;
  if (!root.contains(event.target as Node)) return;
  const items = [...root.querySelectorAll<HTMLElement>(focusable)].filter(
    (item) => !item.hasAttribute("hidden") && item.tabIndex !== -1,
  );
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}
/**
 * Right-side panel shared by the bed detail, the patient record and the history.
 * Esc closes it, Tab stays inside it, and focus goes back to what opened it.
 */
export function Drawer({
  visible,
  onClose,
  title,
  label,
  footer,
  wide = false,
  children,
}: {
  visible: boolean;
  onClose: () => void;
  title: ReactNode;
  label: string;
  footer?: ReactNode;
  wide?: boolean;
  children: ReactNode;
}) {
  const reduced = useReducedMotion();
  const opener = useRef<HTMLElement | null>(null);
  const wasVisible = useRef(false);
  const restore = () => {
    const target = opener.current;
    if (target?.isConnected) target.focus();
    else document.getElementById("main")?.focus();
    opener.current = null;
  };
  // A drawer that is unmounted while open (its owner drops it) also gives focus back.
  const restoreOnUnmount = useRef(restore);
  restoreOnUnmount.current = restore;
  useEffect(
    () => () => {
      if (wasVisible.current) restoreOnUnmount.current();
    },
    [],
  );
  useEffect(() => {
    if (visible && !wasVisible.current) {
      const active = document.activeElement;
      opener.current =
        active instanceof HTMLElement && active !== document.body
          ? active
          : null;
    }
    if (!visible && wasVisible.current) restore();
    wasVisible.current = visible;
  }, [visible]);
  const width = wide ? "w-[min(720px,100vw)]" : "w-[26rem] max-w-full";
  return (
    <Sidebar
      visible={visible}
      onHide={onClose}
      position="right"
      blockScroll
      transitionOptions={{
        classNames: drawerTransition(reduced, "right"),
        timeout: drawerTimeout(reduced),
      }}
      header={
        <h2 className="min-w-0 break-words text-xl font-semibold">{title}</h2>
      }
      pt={{
        root: {
          className: `relative flex h-full flex-col bg-surface text-ink shadow-overlay ${width}`,
          role: "dialog",
          "aria-modal": true,
          "aria-label": label,
          onKeyDown: trapTab,
        },
        mask: { className: "flex justify-end bg-ink/30" },
        header: {
          className:
            "flex items-center justify-between gap-3 border-b border-line p-4",
        },
        icons: { className: "flex shrink-0" },
        closeButton: {
          "aria-label": "Cerrar panel",
          className: `grid h-9 w-9 place-items-center rounded-lg text-ink-muted hov:bg-surface-sunken max-sm:h-11 max-sm:w-11 ${interaction} ${focus}`,
        },
        content: { className: "flex min-h-0 grow flex-col overflow-hidden" },
      }}
      ptOptions={{ mergeSections: false, mergeProps: false }}
    >
      <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
      {footer && (
        <div className="flex shrink-0 flex-wrap justify-end gap-3 border-t border-line bg-surface p-4">
          {footer}
        </div>
      )}
    </Sidebar>
  );
}
