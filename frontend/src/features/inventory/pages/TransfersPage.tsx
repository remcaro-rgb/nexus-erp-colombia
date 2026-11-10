// Movimientos › Traslados (F-INV-REQ T-INV-REQ-04): list with the allowed state actions (submit/approve/ship/receive/cancel).
import { useState } from "react";
import type { TransferAction } from "../api";
import { transferActions } from "../model";
import { errorText, useInventoryApi, useLoad } from "./useInventory";

const LABEL: Record<TransferAction, string> = { submit: "Enviar", approve: "Aprobar", ship: "Despachar", receive: "Recibir", cancel: "Anular" };

export default function TransfersPage() {
  const api = useInventoryApi();
  const [message, setMessage] = useState<string | null>(null);
  const list = useLoad((s) => api.transfers(s), [api]);

  async function act(id: string, action: TransferAction) {
    try {
      const t = await api.transferAction(id, action);
      setMessage(`Traslado ${t.number}: ${t.status}`);
      list.reload();
    } catch (err) {
      setMessage(errorText(err));
    }
  }

  return (
    <section className="nx-card flex flex-col gap-3" aria-labelledby="inv-trf-title">
      <h2 id="inv-trf-title" className="text-base font-semibold text-fg">Traslados entre bodegas</h2>
      {list.error && <p role="alert">{list.error}</p>}
      <table className="nx-table w-full text-sm">
        <caption className="sr-only">Traslados</caption>
        <thead>
          <tr>
            <th scope="col">Número</th>
            <th scope="col">Fecha</th>
            <th scope="col">Estado</th>
            <th scope="col">Acciones</th>
          </tr>
        </thead>
        <tbody>
          {(list.data?.items ?? []).map((t) => (
            <tr key={t.id}>
              <td>{t.number}</td>
              <td>{t.document_date}</td>
              <td>{t.status}</td>
              <td className="flex gap-1">
                {transferActions(t.status).map((a) => (
                  <button key={a} type="button" className="nx-btn" onClick={() => act(t.id, a)} aria-label={`${LABEL[a]} ${t.number}`}>
                    {LABEL[a]}
                  </button>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
