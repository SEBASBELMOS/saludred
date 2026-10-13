import { useEffect } from "react";

/** True when a key press is going into a text field, a select or an editable area. */
export function isTypingTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
  );
}
/** "/" jumps to the main search of the current screen (the field marked data-main-search). */
export function focusMainSearch(event: KeyboardEvent) {
  if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey)
    return false;
  if (isTypingTarget(event.target)) return false;
  // Behind an open dialog or drawer the shortcut stays quiet.
  if (document.querySelector('[role="dialog"]')) return false;
  const field = document.querySelector<HTMLInputElement>("[data-main-search]");
  if (!field) return false;
  event.preventDefault();
  field.focus();
  field.select();
  return true;
}
export function useSearchShortcut() {
  useEffect(() => {
    window.addEventListener("keydown", focusMainSearch);
    return () => window.removeEventListener("keydown", focusMainSearch);
  }, []);
}
