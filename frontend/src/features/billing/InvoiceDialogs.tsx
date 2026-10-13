import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { billingApi, type Factura } from "../../api/billing";
import { patientsApi } from "../../api/patients";
import { cop, fullName } from "../../components/format";
import {
  Action,
  Field,
  FormActions,
  Modal,
  Notice,
  SelectField,
  TextField,
  useToast,
} from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canResolvePatients } from "../auth/permissions";
import { billingMessage, isConflict, servicioNames } from "./labels";
import { useConflictHandler, useRefreshBilling } from "./useBillingFailure";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function PayDialog({
  invoice,
  onClose,
}: {
  invoice: Factura;
  onClose: () => void;
}) {
  const [nota, setNota] = useState("");
  const toast = useToast();
  const refresh = useRefreshBilling();
  const conflict = useConflictHandler();
  const mutation = useMutation({
    mutationFn: () => billingApi.pay(invoice.id, nota.trim() || undefined),
    onSuccess: async () => {
      toast(`Pago registrado: ${cop(invoice.valor)}`);
      onClose();
      await refresh();
    },
    onError: (error) => {
      if (conflict(error)) onClose();
    },
  });
  return (
    <Modal title="Registrar pago" onClose={onClose}>
      <p className="text-sm text-ink-muted">
        {invoice.descripcion}
        <strong className="ml-2 font-semibold tabular-nums text-ink">
          {cop(invoice.valor)}
        </strong>
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        {mutation.error && !isConflict(mutation.error) && (
          <Notice>{billingMessage(mutation.error)}</Notice>
        )}
        <div className="mt-3">
          <TextField
            label="Nota (opcional)"
            maxLength={300}
            rows={2}
            value={nota}
            onChange={(event) => setNota(event.target.value)}
          />
        </div>
        <FormActions
          label="Registrar pago"
          pending={mutation.isPending}
          onClose={onClose}
        />
      </form>
    </Modal>
  );
}

// Two steps: the reason first, then an explicit confirmation, because a void is final.
export function VoidDialog({
  invoice,
  onClose,
}: {
  invoice: Factura;
  onClose: () => void;
}) {
  const [motivo, setMotivo] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [touched, setTouched] = useState(false);
  const toast = useToast();
  const refresh = useRefreshBilling();
  const conflict = useConflictHandler();
  const valid = motivo.trim().length >= 3;
  const mutation = useMutation({
    mutationFn: () => billingApi.void(invoice.id, motivo.trim()),
    onSuccess: async () => {
      toast("Factura anulada");
      onClose();
      await refresh();
    },
    onError: (error) => {
      if (conflict(error)) onClose();
    },
  });
  return (
    <Modal title="Anular factura" onClose={onClose}>
      <p className="text-sm text-ink-muted">
        {invoice.descripcion}
        <strong className="ml-2 font-semibold tabular-nums text-ink">
          {cop(invoice.valor)}
        </strong>
      </p>
      {mutation.error && <Notice>{billingMessage(mutation.error)}</Notice>}
      {confirming ? (
        <div>
          <p className="my-4 text-sm">
            Vas a anular esta factura. Una factura anulada no se puede volver a
            abrir ni pagar.
          </p>
          <p className="text-sm text-ink-muted">Motivo: {motivo.trim()}</p>
          <div className="mt-6 flex flex-wrap justify-end gap-3 border-t border-line pt-4">
            <Action
              secondary
              label="Volver"
              disabled={mutation.isPending}
              onClick={() => setConfirming(false)}
            />
            <Action
              danger
              label="Sí, anular factura"
              loading={mutation.isPending}
              disabled={mutation.isPending}
              onClick={() => mutation.mutate()}
            />
          </div>
        </div>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setTouched(true);
            if (valid) setConfirming(true);
          }}
        >
          <div className="mt-3">
            <TextField
              label="Motivo (obligatorio, queda en el historial)"
              maxLength={300}
              rows={3}
              value={motivo}
              aria-invalid={touched && !valid}
              onChange={(event) => setMotivo(event.target.value)}
            />
            {touched && !valid && (
              <p role="alert" className="mt-1 text-sm text-critical-ink">
                Escribe el motivo de la anulación (mínimo 3 caracteres).
              </p>
            )}
          </div>
          <FormActions danger label="Continuar" onClose={onClose} />
        </form>
      )}
    </Modal>
  );
}

// The admin can search patients; the accountant role cannot, so it pastes the id.
function PatientPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const { user } = useAuth();
  const [term, setTerm] = useState("");
  const [picked, setPicked] = useState("");
  const canSearch = !!user && canResolvePatients(user.role);
  const query = term.trim();
  const key = /^\d+$/.test(query) ? "document_number" : "name";
  const ready =
    key === "document_number" ? query.length >= 6 : query.length >= 3;
  const found = useQuery({
    queryKey: ["billing-patient-search", key, query],
    queryFn: ({ signal }) => patientsApi.search(key, query, signal),
    enabled: canSearch && ready,
    meta: { silent: true },
  });
  if (!canSearch)
    return (
      <div>
        <Field
          label="Identificador del paciente"
          value={value}
          autoComplete="off"
          spellCheck={false}
          placeholder="00000000-0000-0000-0000-000000000000"
          onChange={(event) => onChange(event.target.value.trim())}
        />
        <p className="mt-1 text-xs text-ink-muted">
          Tu rol no consulta datos de pacientes, por eso la factura se crea con
          el identificador. Pídelo a la IPS o a la administración y pégalo aquí.
        </p>
      </div>
    );
  return (
    <div>
      <Field
        label="Buscar paciente por nombre o documento"
        value={term}
        autoComplete="off"
        onChange={(event) => setTerm(event.target.value)}
      />
      {picked && (
        <p className="mt-2 text-sm">
          Paciente elegido: <strong className="font-semibold">{picked}</strong>
        </p>
      )}
      {ready && found.data && (
        <ul className="mt-2 divide-y divide-line rounded-lg border border-line">
          {found.data.items.length === 0 && (
            <li className="px-3 py-2 text-sm text-ink-muted">
              Sin resultados.
            </li>
          )}
          {found.data.items.map((patient) => (
            <li key={patient.id}>
              <button
                type="button"
                className="flex min-h-9 w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-sm hov:bg-surface-sunken max-sm:min-h-11"
                onClick={() => {
                  onChange(patient.id);
                  setPicked(fullName(patient));
                  setTerm("");
                }}
              >
                <span className="font-semibold">{fullName(patient)}</span>
                <span className="text-ink-muted">
                  {patient.document_number}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function NewInvoiceDialog({ onClose }: { onClose: () => void }) {
  const [servicio, setServicio] = useState<"consulta" | "medicamento">(
    "consulta",
  );
  const [pacienteId, setPacienteId] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [cantidad, setCantidad] = useState("1");
  const [valor, setValor] = useState("");
  const toast = useToast();
  const refresh = useRefreshBilling();
  const mutation = useMutation({
    mutationFn: () =>
      billingApi.create({
        paciente_id: pacienteId,
        servicio,
        descripcion: descripcion.trim(),
        cantidad: Number(cantidad),
        ...(valor ? { valor_unitario: Number(valor) } : {}),
      }),
    onSuccess: async (invoice) => {
      toast(`Factura creada: ${cop(invoice.valor)}`);
      onClose();
      await refresh();
    },
  });
  const qty = Number(cantidad);
  const price = Number(valor);
  const priceOk =
    servicio === "consulta"
      ? !valor || price >= 1
      : Number.isInteger(price) && price >= 1;
  const valid =
    UUID.test(pacienteId) &&
    descripcion.trim().length >= 3 &&
    Number.isInteger(qty) &&
    qty >= 1 &&
    priceOk;
  return (
    <Modal title="Nueva factura" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (valid) mutation.mutate();
        }}
      >
        {mutation.error && <Notice>{billingMessage(mutation.error)}</Notice>}
        <SelectField
          label="Servicio"
          value={servicio}
          onChange={(event) =>
            setServicio(event.target.value as "consulta" | "medicamento")
          }
        >
          <option value="consulta">{servicioNames.consulta}</option>
          <option value="medicamento">{servicioNames.medicamento}</option>
        </SelectField>
        <PatientPicker value={pacienteId} onChange={setPacienteId} />
        <Field
          label="Descripción"
          maxLength={300}
          value={descripcion}
          onChange={(event) => setDescripcion(event.target.value)}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Cantidad"
            type="number"
            inputMode="numeric"
            min={1}
            max={999}
            value={cantidad}
            onChange={(event) => setCantidad(event.target.value)}
          />
          <div>
            <Field
              label={
                servicio === "consulta"
                  ? "Valor unitario (COP, opcional)"
                  : "Valor unitario (COP)"
              }
              type="number"
              inputMode="numeric"
              min={1}
              value={valor}
              onChange={(event) => setValor(event.target.value)}
            />
            {servicio === "consulta" && (
              <p className="mt-1 text-xs text-ink-muted">
                Si lo dejas vacío se usa la tarifa de consulta.
              </p>
            )}
          </div>
        </div>
        <FormActions
          label="Crear factura"
          pending={mutation.isPending}
          disabled={!valid}
          onClose={onClose}
        />
      </form>
    </Modal>
  );
}
