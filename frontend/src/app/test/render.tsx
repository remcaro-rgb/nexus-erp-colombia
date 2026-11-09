// Test render helper (owner P08): design-system providers + an in-memory router.
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { DesignSystemProvider } from "@ds/DesignSystemProvider";

export function renderDs(ui: ReactElement, opts: RenderOptions & { route?: string } = {}) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[opts.route ?? "/"]}>
      <DesignSystemProvider theme="light">{children}</DesignSystemProvider>
    </MemoryRouter>
  );
  return render(ui, { wrapper: Wrapper, ...opts });
}
