const TOKEN_KEY = "saludred.token";
export const sessionToken = {
  get: () => {
    try {
      return sessionStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set: (value: string) => {
    try {
      sessionStorage.setItem(TOKEN_KEY, value);
    } catch {
      /* Storage can be disabled by the browser. */
    }
  },
  clear: () => {
    try {
      sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      /* Storage can be disabled by the browser. */
    }
  },
};
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
const expirationListeners = new Set<() => void>();
export function onSessionExpired(listener: () => void) {
  expirationListeners.add(listener);
  return () => {
    expirationListeners.delete(listener);
  };
}
function errorMessage(data: unknown, status: number): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail
        .map((item) => `${item.loc?.slice(1).join(".") || "dato"}: ${item.msg}`)
        .join("; ");
  }
  return `La API respondió con el código ${status}.`;
}
interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  raw?: boolean;
}
export async function request(
  path: string,
  options: RequestOptions = {},
): Promise<Response> {
  const { body, raw, ...init } = options;
  const headers = new Headers(init.headers);
  const token = sessionToken.get();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (body !== undefined && !raw)
    headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers,
      body:
        body === undefined
          ? undefined
          : raw
            ? (body as BodyInit)
            : JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError(
      0,
      "No hay conexión con la API. Revisa que el servicio esté encendido.",
    );
  }
  if (response.status === 401 && path !== "/auth/login") {
    sessionToken.clear();
    expirationListeners.forEach((listener) => listener());
    throw new ApiError(401, "Tu sesión terminó. Vuelve a iniciar sesión.");
  }
  if (!response.ok)
    throw new ApiError(
      response.status,
      errorMessage(await response.json().catch(() => null), response.status),
    );
  return response;
}
export async function api<T>(
  path: string,
  options?: RequestOptions,
): Promise<T> {
  const response = await request(path, options);
  return response.status === 204
    ? (null as T)
    : (response.json() as Promise<T>);
}
export const queryString = (
  values: Record<string, string | number | undefined>,
) =>
  new URLSearchParams(
    Object.entries(values)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();
