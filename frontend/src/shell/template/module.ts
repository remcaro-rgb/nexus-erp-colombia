// TEMPLATE descriptor (owner P08). Not auto-registered from here: copy to features/<module>/module.ts.
import { lazy } from "react";
import type { ModuleDescriptor } from "../registry";

const descriptor: ModuleDescriptor = {
  id: "__template__",
  pages: {
    listado: lazy(() => import("./ExampleListPage")),
    documento: lazy(() => import("./ExampleDocumentPage")),
  },
  actions: [],
};

export default descriptor;
