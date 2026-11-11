// Keyboard-wedge scan input (F-INV-BARCODE): the scanner types the code and sends Enter; the value is resolved through
// GET /barcodes/lookup (item / presentation / location / lot). A scan only fills a document line, it never posts.
import { useState } from "react";
import { ApiError } from "@lib/api/client";
import type { BarcodesApi, ScanResolution } from "./api";
import { normalizeScan } from "./model";

export interface ScanInputProps {
  api: BarcodesApi;
  onResolved: (r: ScanResolution) => void;
  label?: string;
  id?: string;
}

export default function ScanInput({ api, onResolved, label = "Escanear código de barras", id = "nx-scan" }: ScanInputProps) {
  const [value, setValue] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  async function resolve() {
    const v = normalizeScan(value);
    if (!v) return;
    try {
      const r = await api.lookup(v);
      setStatus(`${r.data.code ?? v}: ${r.data.name ?? r.data.owner_type}`);
      setValue("");
      onResolved(r.data);
    } catch (err) {
      setStatus(err instanceof ApiError && err.code === "barcode_unknown" ? `Código no reconocido: ${v}` : String(err));
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium text-fg">
        {label}
      </label>
      <input
        id={id}
        className="nx-input"
        value={value}
        autoComplete="off"
        inputMode="text"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === "Tab") {
            if (normalizeScan(value)) e.preventDefault();
            void resolve();
          }
        }}
        aria-describedby={`${id}-status`}
      />
      <p id={`${id}-status`} role="status" className="text-xs text-fg-muted">
        {status ?? "Escanee o escriba el código y presione Enter"}
      </p>
    </div>
  );
}
