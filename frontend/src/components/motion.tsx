import {
  Children,
  cloneElement,
  isValidElement,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";

const media = "(prefers-reduced-motion: reduce)";
function subscribe(callback: () => void) {
  const query = window.matchMedia(media);
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}
export function useReducedMotion() {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(media).matches,
    () => true,
  );
}
export function motionDuration(token: string) {
  return (
    Number(
      getComputedStyle(document.documentElement).getPropertyValue(
        `--motion-${token}`,
      ),
    ) || 0
  );
}
// Button feedback only, never on cards or rows: colour, border and focus-ring changes
// run in 160 ms ease-out, and the press shrinks the button to 0.97.
export const interaction =
  "transition-[color,background-color,border-color,outline-color,outline-offset,transform] duration-press ease-out motion-safe:enabled:active:scale-[0.97] disabled:pointer-events-none";
export const overlayTransition = {
  enter: "opacity-0 motion-safe:scale-[0.97]",
  enterActive:
    "!opacity-100 motion-safe:!scale-100 transition-[opacity,transform] duration-base ease-out",
  exit: "opacity-100 motion-safe:scale-100",
  exitActive:
    "!opacity-0 motion-safe:!scale-[0.97] transition-[opacity,transform] duration-exit ease-out",
};
export const toastTransition = {
  enter: "opacity-0 motion-safe:-translate-y-2",
  enterActive:
    "!opacity-100 motion-safe:!translate-y-0 transition-[opacity,transform] duration-base ease-out",
  exit: "opacity-100",
  exitActive: "!opacity-0 transition-opacity duration-exit ease-out",
};
// Side panel: slides in over 280 ms with the drawer curve and leaves faster.
// With reduced motion it only fades.
export const drawerTransition = (
  reduced: boolean,
  side: "left" | "right" = "right",
) => {
  const out = side === "right" ? "translate-x-full" : "-translate-x-full";
  return reduced
    ? {
        enter: "opacity-0",
        enterActive: "!opacity-100 transition-opacity duration-exit ease-out",
        exit: "opacity-100",
        exitActive: "!opacity-0 transition-opacity duration-exit ease-out",
      }
    : {
        enter: out,
        enterActive:
          "!translate-x-0 transition-transform duration-drawer ease-drawer",
        exit: "translate-x-0",
        exitActive: `!${out} transition-transform duration-base ease-out`,
      };
};
export const drawerTimeout = (reduced: boolean) =>
  reduced
    ? 0
    : { enter: motionDuration("drawer"), exit: motionDuration("base") };
export function useChangeHighlight(value: string) {
  const previous = useRef(value);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (previous.current !== value) {
      previous.current = value;
      setRevision((v) => v + 1);
    }
  }, [value]);
  return revision
    ? revision % 2
      ? "animate-flash"
      : "animate-flash-again"
    : "";
}
function contentSignature(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(contentSignature).join("|");
  if (!isValidElement<Record<string, unknown>>(node)) return "";
  return Object.entries(node.props)
    .filter(
      ([key, value]) =>
        key === "children" ||
        (typeof value !== "function" &&
          ["string", "number"].includes(typeof value) &&
          !["className", "id"].includes(key)),
    )
    .map(([key, value]) =>
      key === "children" ? contentSignature(value as ReactNode) : String(value),
    )
    .join("|");
}
function MotionItem({
  child,
}: {
  child: ReactElement<{ className?: string; style?: CSSProperties }>;
}) {
  const changed = useChangeHighlight(contentSignature(child));
  return changed
    ? cloneElement(child, {
        className: `${child.props.className || ""} ${changed}`.trim(),
      })
    : child;
}
// Rows only flash when their content changed after a refresh; they never animate in.
export function MotionItems({ children }: { children: ReactNode }) {
  return Children.toArray(children).map((child, index) =>
    isValidElement<{ className?: string; style?: CSSProperties }>(child) ? (
      <MotionItem key={child.key ?? index} child={child} />
    ) : (
      child
    ),
  );
}
