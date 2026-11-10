// es-CO microcopy of the Contabilidad feature (owner P05). Error codes follow contracts/errors.yaml (module ledger).
import { ApiError } from "@lib/api/client";

const nf = new Intl.NumberFormat("es-CO");

export const msg = {
  title: "Dimensiones contables",
  subtitle: "Maestros analíticos por empresa: solo los nodos Detalle activos reciben imputaciones.",
  newGroup: "Nuevo agrupador",
  newLeaf: "Nuevo detalle",
  move: "Mover",
  archive: "Archivar",
  unarchive: "Desarchivar",
  moveLabel: (code: string) => `Mover ${code}`,
  archiveLabel: (code: string) => `Archivar ${code}`,
  unarchiveLabel: (code: string) => `Desarchivar ${code}`,
  retry: "Reintentar",
  loading: "Cargando…",
  empty: (label: string) => `Aún no hay ${label.toLocaleLowerCase("es-CO")}. Cree un agrupador o un detalle para empezar.`,
  createGroupTitle: (s: string) => `Nuevo agrupador de ${s}`,
  createLeafTitle: (s: string) => `Nuevo detalle de ${s}`,
  createHint: "El código es único por empresa (sin distinguir mayúsculas). No use datos personales en códigos ni descripciones.",
  code: "Código",
  codeHint: "De 1 a 40 caracteres.",
  description: "Descripción",
  parent: "Agrupador padre",
  parentHint: "Solo un Agrupador activo de la misma dimensión puede ser padre.",
  rootOption: "(Sin padre: nodo raíz)",
  newParent: "Nuevo agrupador padre",
  moveTitle: (code: string) => `Mover ${code}`,
  moveHint: "Mover cambia solo la agrupación; los movimientos contabilizados no se modifican.",
  archiveTitle: (code: string) => `Archivar ${code}`,
  archiveHint: "Archivar bloquea nuevas imputaciones; los movimientos ya contabilizados se conservan.",
  usageLoading: "Consultando uso…",
  usage: (n: number) => (n === 0 ? "Sin movimientos contabilizados." : `${nf.format(n)} líneas contabilizadas usan este nodo.`),
  reason: "Motivo",
  cancel: "Cancelar",
  save: "Guardar",
  created: (code: string) => `Se creó ${code}.`,
  moved: (code: string, path: string) => `Se movió ${code} a ${path}.`,
  archived: (code: string) => `Se archivó ${code}.`,
  unarchived: (code: string) => `Se desarchivó ${code}.`,
};

const ERRORS: Record<string, string> = {
  dimension_code_duplicate: "Ya existe un nodo con ese código en esta dimensión.",
  dimension_cycle: "No se puede mover un nodo debajo de sí mismo o de sus descendientes.",
  dimension_depth_exceeded: "Se supera la profundidad máxima configurada para esta dimensión.",
  dimension_cross_company: "El nodo indicado no pertenece a esta empresa.",
  dimension_archived: "El nodo (o su padre) está archivado.",
  dimension_in_use: "El nodo tiene movimientos; archívelo en lugar de eliminarlo.",
  dimension_node_not_postable: "Un Agrupador no recibe imputaciones; elija un Detalle.",
  validation_failed: "Revise los datos del formulario.",
  policy_denied: "No tiene permiso para esta acción.",
  not_authenticated: "Su sesión terminó; vuelva a iniciar sesión.",
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return ERRORS[e.code] ?? e.message;
  if (e instanceof Error && e.name === "AbortError") return "";
  return "No fue posible completar la operación. Intente de nuevo.";
}
