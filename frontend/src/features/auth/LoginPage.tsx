import { useState, type FormEvent } from "react";
import { ApiError } from "../../api/client";
import { Action, Field, Notice } from "../../components/ui";
import { NetworkIllustration } from "./NetworkIllustration";
import { useAuth } from "./AuthProvider";
export const loginError = (error: Error) =>
  error instanceof ApiError && error.status === 401
    ? "Usuario o contraseña incorrectos. Al tercer intento fallido la cuenta se bloquea."
    : error instanceof ApiError && error.status === 423
      ? "Cuenta bloqueada por intentos fallidos. Solicite el desbloqueo a un administrador."
      : error.message;
export function LoginForm({
  login,
  message = "",
}: {
  login: (username: string, password: string) => Promise<void>;
  message?: string;
}) {
  const [error, setError] = useState<Error | null>(null);
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const username = String(data.get("username")).trim();
    const password = String(data.get("password"));
    if (!username || !password) {
      setError(new Error("Escribe el usuario y la contraseña."));
      return;
    }
    setPending(true);
    setError(null);
    try {
      await login(username, password);
    } catch (e) {
      setError(e as Error);
      const field = form.elements.namedItem("password") as HTMLInputElement;
      field.value = "";
      field.focus();
    } finally {
      setPending(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-5">
      <Field
        label="Usuario"
        name="username"
        autoComplete="username"
        required
        autoFocus
      />
      <Field
        label="Contraseña"
        name="password"
        type="password"
        autoComplete="current-password"
        required
      />
      {(error || message) && (
        <Notice amber={error instanceof ApiError && error.status === 423}>
          {error ? loginError(error) : message}
        </Notice>
      )}
      <Action
        type="submit"
        label={pending ? "Verificando…" : "Ingresar"}
        loading={pending}
        disabled={pending}
        className="w-full"
      />
      <p className="text-xs leading-relaxed text-ink-muted">
        Después de tres intentos fallidos la cuenta se bloquea y solo la
        administración puede desbloquearla.
      </p>
    </form>
  );
}
export function LoginPage() {
  const auth = useAuth();
  return (
    <main id="main" className="grid min-h-dvh lg:grid-cols-[1.15fr_1fr]">
      <section className="relative isolate flex flex-col justify-between overflow-hidden bg-accent-strong p-8 text-white lg:p-16">
        <NetworkIllustration />
        <div className="relative flex items-center gap-3">
          <img
            src="/logo.svg"
            alt=""
            className="h-11 w-11 rounded-xl bg-surface p-1"
          />
          <span className="font-display text-xl font-semibold">
            salud<span className="font-sans font-normal">red</span>
          </span>
        </div>
        <div className="relative my-10 max-w-md">
          <h1 className="font-display text-display font-semibold">
            Cada cama cuenta.
          </h1>
          <p className="mt-4 max-w-xs text-lg text-white/85">
            Una red de instituciones. Una misma visión de la capacidad para
            cuidar.
          </p>
        </div>
      </section>
      <section className="flex items-center justify-center p-6 sm:p-12">
        <div className="w-full max-w-sm">
          <h2 className="text-xl font-semibold">Ingresa a tu espacio</h2>
          <p className="mb-6 mt-1 text-ink-muted">
            Usa la cuenta asignada por tu institución.
          </p>
          <LoginForm login={auth.login} message={auth.message} />
          <p className="mt-8 border-t border-line pt-5 text-xs leading-relaxed text-ink-muted">
            <strong>Aviso de privacidad.</strong> SaludRed trata datos de salud,
            que son datos sensibles según la Ley 1581 de 2012. Solo el personal
            autorizado accede, cada acceso queda registrado y la información se
            usa únicamente para coordinar la atención. Los datos de este entorno
            son sintéticos.
          </p>
        </div>
      </section>
    </main>
  );
}
