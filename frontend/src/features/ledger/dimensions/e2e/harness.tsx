// e2e harness (T-ACC-DIM-08): the real DimensionsPage inside the P08 design-system providers, as the shell mounts it.
// Used only by the Playwright spec next to it; never part of the production bundle (not referenced by index.html).
import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import "@ds/styles.css";
import { DesignSystemProvider } from "@ds/DesignSystemProvider";
import { DimensionsPage } from "../DimensionsPage";

function Harness() {
  const params = new URLSearchParams(window.location.search);
  const [tab, setTab] = useState(params.get("tab") ?? "centros-costo");
  return (
    <main className="min-h-screen bg-app p-6">
      <DimensionsPage
        tab={tab}
        companyId={params.get("company")}
        onTabChange={(t) => {
          setTab(t);
          window.history.replaceState(null, "", `?tab=${t}&company=${params.get("company") ?? ""}`);
        }}
      />
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DesignSystemProvider theme="light">
      <Harness />
    </DesignSystemProvider>
  </StrictMode>,
);
