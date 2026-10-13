import { Action } from "./ui";
export function ErrorState({
  error,
  retry,
}: {
  error: Error;
  retry: () => unknown;
}) {
  return (
    <div
      role="alert"
      className="m-4 rounded-xl border border-critical/30 bg-critical-tint p-5 text-critical-ink"
    >
      <h3 className="font-semibold">No se pudo cargar esta información</h3>
      <p className="my-3 text-sm">{error.message}</p>
      <Action secondary label="Reintentar" onClick={() => void retry()} />
    </div>
  );
}
