// Route element for /m/contabilidad/dimensiones/:tab (deep link; reloading the URL restores the tab).
import { useNavigate, useParams } from "react-router";
import { DimensionsPage } from "./DimensionsPage";
import { DIMENSIONS_ROUTE, tabById } from "./config";

export default function DimensionsRoute({ companyId }: { companyId?: string | null }) {
  const { tab } = useParams();
  const navigate = useNavigate();
  return <DimensionsPage tab={tabById(tab).tab} companyId={companyId ?? null} onTabChange={(t) => navigate(`${DIMENSIONS_ROUTE}/${t}`)} />;
}
