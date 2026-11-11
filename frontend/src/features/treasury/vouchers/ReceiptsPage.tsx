// Tesorería › Recibos de caja › Recibos (F-TRE-RC).
import VouchersPage from "./VouchersPage";

export default function ReceiptsPage() {
  return <VouchersPage kind="receipts" title="Recibos de caja" perm="treasury.receipt" />;
}
