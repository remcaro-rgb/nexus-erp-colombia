// TEMPLATE list page (owner P08): server data through useResource, DataGrid with field masks, export registration.
import { useT } from "@i18n/index";
import { DataGrid, type GridColumn } from "@ds/data-grid";
import type { Page } from "@lib/api/types";
import { useResource, useShell } from "../ShellContext";
import { useRegisterExport } from "../exportContext";
import type { PageComponent } from "../registry";

interface Row {
  id: string;
  code: string;
  name: string;
  amount: string;
}

const ExampleListPage: PageComponent = ({ module }) => {
  const t = useT();
  const shell = useShell();
  const endpoint = `/api/v1/${module.id}/records`;
  const res = useResource<Page<Row>>(endpoint, { company_id: shell.companyId });
  useRegisterExport({ entity: `${module.id}.records` });
  const columns: GridColumn<Row>[] = [
    { id: "code", header: t("tree.code") },
    { id: "name", header: t("tree.description") },
    { id: "amount", header: t("doc.totals"), type: "COP", total: true },
  ];
  return <DataGrid label={t("template.list")} resource={`${module.id}.record`} rows={res.data?.results ?? []} columns={columns} masks={shell.masks} userId={shell.userId} />;
};

export default ExampleListPage;
