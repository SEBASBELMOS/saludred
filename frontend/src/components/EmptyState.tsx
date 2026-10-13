import type { ReactNode } from "react";
export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="px-6 py-10 text-center">
      <i
        className="pi pi-inbox mb-3 text-xl text-ink-muted"
        aria-hidden="true"
      />
      <h3 className="text-lg font-semibold">{title}</h3>
      <div className="mx-auto mt-2 max-w-md text-sm text-ink-muted">
        {children}
      </div>
    </div>
  );
}
