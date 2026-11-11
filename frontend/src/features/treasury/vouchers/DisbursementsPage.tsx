// Tesorería › Egresos › Comprobantes (F-TRE-CE).
import VouchersPage from "./VouchersPage";

export default function DisbursementsPage() {
  return <VouchersPage kind="disbursements" title="Comprobantes de egreso" perm="treasury.disbursement" />;
}
