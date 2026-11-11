// Inventario › Maestros › Códigos y etiquetas (F-INV-BARCODE): scan/lookup, generate (Code 128 internal or EAN-13 from
// a user-entered GS1 prefix), import/validate existing GTINs, label templates and print jobs with preview + print.
import { useMemo, useState } from "react";
import { useShell } from "@shell/ShellContext";
import { ApiError } from "@lib/api/client";
import { InventoryApi, type ItemDto } from "../api";
import { BarcodesApi, type LabelJobDto, type OwnerType, type ScanResolution } from "./api";
import LabelPreview from "./LabelPreview";
import { isRestrictedCirculation, isValidGtin } from "./model";
import ScanInput from "./ScanInput";

function errorText(e: unknown): string {
  return e instanceof ApiError ? `${e.code}: ${e.message}` : String(e);
}

export default function BarcodesPage() {
  const shell = useShell();
  const api = useMemo(() => new BarcodesApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
  const inv = useMemo(() => new InventoryApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
  const [scan, setScan] = useState<ScanResolution | null>(null);
  const [items, setItems] = useState<ItemDto[]>([]);
  const [query, setQuery] = useState("");
  const [ownerType, setOwnerType] = useState<OwnerType>("item");
  const [ownerId, setOwnerId] = useState("");
  const [symbology, setSymbology] = useState<"CODE128" | "EAN13">("CODE128");
  const [prefix, setPrefix] = useState("");
  const [gtin, setGtin] = useState("");
  const [job, setJob] = useState<LabelJobDto | null>(null);
  const [copies, setCopies] = useState(1);
  const [message, setMessage] = useState<string | null>(null);

  async function search() {
    try {
      setItems((await inv.items(query)).items);
    } catch (e) {
      setMessage(errorText(e));
    }
  }

  async function generate() {
    try {
      const r = await api.generate(ownerType, ownerId, symbology, symbology === "EAN13" ? prefix : undefined);
      setMessage(`Código generado ${r.result.value}${r.result.label ? ` (${r.result.label})` : ""}`);
    } catch (e) {
      setMessage(errorText(e));
    }
  }

  async function importGtin() {
    if (!isValidGtin(gtin)) {
      setMessage("barcode_checkdigit_invalid: dígito de control inválido");
      return;
    }
    try {
      const r = await api.importGtin(ownerType, ownerId, gtin);
      setMessage(`GTIN importado ${r.result.value} (no se reclama propiedad GS1)`);
    } catch (e) {
      setMessage(errorText(e));
    }
  }

  async function print() {
    try {
      const templates = (await api.templates()).items;
      const tpl =
        templates.find((t) => t.status === "ACTIVE") ??
        (await api.createTemplate({ code: "ESTANDAR-50x25", name: "Etiqueta estándar 50×25", symbology: "CODE128", layout: { fields: ["value", "code", "name"] }, size_mm: "50x25" }));
      const key = ({ item: "items", presentation: "presentations", location: "locations", lot: "lots" } as const)[ownerType];
      setJob(await api.printJob(tpl.id, { [key]: [{ id: ownerId, copies }] }));
      setMessage(null);
    } catch (e) {
      setMessage(errorText(e));
    }
  }

  return (
    <section className="nx-card flex flex-col gap-4" aria-labelledby="inv-bc-title">
      <h2 id="inv-bc-title" className="text-base font-semibold text-fg">Códigos de barras y etiquetas</h2>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">Escanear</h3>
          <ScanInput api={api} onResolved={setScan} id="bc-scan" />
          {scan && (
            <dl className="text-sm">
              <dt className="font-medium">Tipo</dt>
              <dd>{scan.owner_type}</dd>
              <dt className="font-medium">Código</dt>
              <dd>{scan.code ?? "—"}</dd>
              <dt className="font-medium">Descripción</dt>
              <dd>{scan.name ?? "—"}</dd>
            </dl>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">Destino</h3>
          <label className="text-sm" htmlFor="bc-owner-type">Tipo de destino</label>
          <select id="bc-owner-type" className="nx-input" value={ownerType} onChange={(e) => setOwnerType(e.target.value as OwnerType)}>
            <option value="item">Producto</option>
            <option value="presentation">Presentación</option>
            <option value="location">Ubicación</option>
            <option value="lot">Lote</option>
          </select>
          <label className="text-sm" htmlFor="bc-item-q">Buscar producto</label>
          <div className="flex gap-1">
            <input id="bc-item-q" className="nx-input" value={query} onChange={(e) => setQuery(e.target.value)} />
            <button type="button" className="nx-btn" onClick={() => void search()}>Buscar</button>
          </div>
          {items.length > 0 && (
            <ul className="text-sm" aria-label="Productos encontrados">
              {items.map((i) => (
                <li key={i.id}>
                  <button type="button" className="nx-link" onClick={() => setOwnerId(i.id)} aria-pressed={ownerId === i.id}>
                    {i.code} — {i.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <label className="text-sm" htmlFor="bc-owner-id">Id del destino</label>
          <input id="bc-owner-id" className="nx-input font-mono" value={ownerId} onChange={(e) => setOwnerId(e.target.value.trim())} />
        </div>
      </div>

      <fieldset className="flex flex-wrap items-end gap-2">
        <legend className="text-sm font-semibold">Generar</legend>
        <label className="text-sm" htmlFor="bc-sym">Simbología</label>
        <select id="bc-sym" className="nx-input" value={symbology} onChange={(e) => setSymbology(e.target.value as "CODE128" | "EAN13")}>
          <option value="CODE128">Code 128 (interno)</option>
          <option value="EAN13">EAN-13 (prefijo GS1 de la empresa)</option>
        </select>
        {symbology === "EAN13" && (
          <>
            <label className="text-sm" htmlFor="bc-prefix">Prefijo GS1 (lo ingresa el usuario)</label>
            <input id="bc-prefix" className="nx-input w-32" inputMode="numeric" value={prefix} onChange={(e) => setPrefix(e.target.value.trim())} />
            {isRestrictedCirculation(`${prefix}00000000000`.slice(0, 13)) && <span className="text-xs">Uso interno, no licencia GS1</span>}
          </>
        )}
        <button type="button" className="nx-btn" disabled={!ownerId} onClick={() => void generate()}>Generar código</button>
      </fieldset>

      <fieldset className="flex flex-wrap items-end gap-2">
        <legend className="text-sm font-semibold">Importar GTIN existente</legend>
        <label className="text-sm" htmlFor="bc-gtin">GTIN</label>
        <input id="bc-gtin" className="nx-input w-48" inputMode="numeric" value={gtin} onChange={(e) => setGtin(e.target.value)} aria-invalid={gtin !== "" && !isValidGtin(gtin)} />
        <button type="button" className="nx-btn" disabled={!ownerId || !gtin} onClick={() => void importGtin()}>Validar e importar</button>
      </fieldset>

      <fieldset className="flex flex-wrap items-end gap-2">
        <legend className="text-sm font-semibold">Etiquetas</legend>
        <label className="text-sm" htmlFor="bc-copies">Copias</label>
        <input id="bc-copies" className="nx-input w-20" type="number" min={1} max={500} value={copies} onChange={(e) => setCopies(Math.max(1, Math.min(500, Number.parseInt(e.target.value || "1", 10))))} />
        <button type="button" className="nx-btn" disabled={!ownerId} onClick={() => void print()}>Vista previa</button>
        {job && (
          <button type="button" className="nx-btn" onClick={() => window.print()}>Imprimir</button>
        )}
      </fieldset>

      {job && (
        <div className="nx-print-area flex flex-wrap gap-2" aria-label={`Vista previa: ${job.label_count} etiquetas`}>
          {job.labels.map((l, n) => (
            // eslint-disable-next-line react/no-array-index-key -- copies of the same label are identical
            <LabelPreview key={`${l.value}-${n}`} label={l} fields={["value", "code", "name", "presentation", "lot", "expiry_date", "location"]} />
          ))}
        </div>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
