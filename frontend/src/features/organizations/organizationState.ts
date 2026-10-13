import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Organization } from "../../api/types";
import { ApiError } from "../../api/client";
export const organizationKeys = {
  eps: ["organizations", "eps"] as const,
  ips: (id: string) => ["organizations", "ips", id] as const,
  history: (id: string) => ["organizations", "history", id] as const,
};
export function useInvalidateOrganizations() {
  const client = useQueryClient();
  return () =>
    Promise.all(
      [
        "organizations",
        "eps",
        "network",
        "analytics",
        "queue",
        "audit",
        "accounts",
      ].map((key) => client.invalidateQueries({ queryKey: [key] })),
    );
}
export function useRecentDeletions(userId: string) {
  const client = useQueryClient();
  const key = ["recently-deleted-organizations", userId];
  const query = useQuery<Organization[]>({
    queryKey: key,
    initialData: [],
    enabled: false,
    gcTime: Infinity,
    queryFn: async () => client.getQueryData<Organization[]>(key) || [],
  });
  return {
    items: query.data || [],
    add: (organization: Organization) =>
      client.setQueryData<Organization[]>(key, (previous) => [
        organization,
        ...(previous || []).filter((item) => item.id !== organization.id),
      ]),
    remove: (id: string) =>
      client.setQueryData<Organization[]>(key, (previous) =>
        (previous || []).filter((item) => item.id !== id),
      ),
  };
}
// The server writes its messages without accents; known ones are rewritten with
// the way forward, and field errors (422) name the field that failed.
export function organizationError(error: Error): string {
  if (error instanceof ApiError) {
    const text = error.message;
    if (error.status === 409) {
      if (/ya existe/i.test(text) && /codigo|código/i.test(text))
        return "Ya existe una organización con ese código. Cada EPS e IPS lleva un código propio: elige otro.";
      if (/no es una EPS/i.test(text))
        return "Una IPS no puede ser la organización padre. Crea la nueva IPS dentro de una EPS.";
      if (/padre debe ser una EPS activa/i.test(text))
        return "La EPS de esta IPS está eliminada o ya no es una EPS. Restaura la EPS y vuelve a intentarlo.";
      if (/ips asociadas/i.test(text))
        return "No se puede eliminar esta EPS mientras tenga IPS activas. Elimina primero sus IPS.";
      return text;
    }
    if (error.status === 422) {
      const problems: string[] = [];
      if (/\bcode\b|codigo|código/i.test(text))
        problems.push("El código debe tener entre 1 y 32 caracteres.");
      if (/\bname\b|nombre/i.test(text))
        problems.push("El nombre debe tener entre 1 y 200 caracteres.");
      if (/must not reference a parent|parent_organization/i.test(text))
        problems.push(
          "Una EPS es la raíz de la red y no tiene organización padre: créala con «Nueva EPS».",
        );
      if (/must reference a parent|must have a parent/i.test(text))
        problems.push(
          "Una IPS necesita una EPS como padre: créala con «Nueva IPS» dentro de su EPS.",
        );
      return problems.length
        ? problems.join(" ")
        : "El servidor rechazó los datos. El código debe tener entre 1 y 32 caracteres y el nombre entre 1 y 200. Revisa ambos campos.";
    }
    if (error.status === 403)
      return "Solo la administración puede realizar esta operación.";
    if (error.status === 404)
      return "La organización no existe o ya no está disponible. Actualiza la vista.";
  }
  return error.message;
}
export function validateOrganization(code: string, name: string) {
  if (!code.trim() || code.length > 32)
    throw new Error("El código debe tener entre 1 y 32 caracteres.");
  if (!name.trim() || name.length > 200)
    throw new Error("El nombre debe tener entre 1 y 200 caracteres.");
  return { code: code.trim(), name: name.trim() };
}
