// Forms: Zod validation, server problem+json mapped to fields, local autosave for non-posting drafts only.
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { z } from "zod";
import { renderDs } from "@app/test/render";
import { ApiError } from "@lib/api/client";
import { useEntityForm, pointerToPath } from "../useEntityForm";

const schema = z.object({ nombre: z.string().min(3, "Mínimo 3 caracteres"), codigo: z.string().min(1, "Obligatorio") });

function Form({ posting = false }: { posting?: boolean }) {
  const { form, submit, serverError } = useEntityForm({
    schema,
    defaultValues: { nombre: "", codigo: "" },
    draftKey: "demo",
    posting,
    userId: "u-1",
    onSubmit: async () => {
      throw new ApiError(422, "validation_failed", { type: "x", title: "Inválido", status: 422, code: "validation_failed", trace_id: "t-1", errors: [{ pointer: "/codigo", detail: "Código duplicado en la empresa" }] }, "t-1");
    },
  });
  const e = form.formState.errors;
  return (
    <form onSubmit={(ev) => void submit(ev)}>
      <label htmlFor="n">Nombre</label>
      <input id="n" {...form.register("nombre")} />
      {e.nombre ? <p role="alert">{e.nombre.message}</p> : null}
      <label htmlFor="c">Código</label>
      <input id="c" {...form.register("codigo")} />
      {e.codigo ? <p role="alert">{e.codigo.message}</p> : null}
      {serverError ? <p>{serverError.traceId}</p> : null}
      <button type="submit">Guardar</button>
    </form>
  );
}

describe("P08 forms", () => {
  it("validates with Zod and maps server field errors", async () => {
    const user = userEvent.setup();
    renderDs(<Form />);
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Mínimo 3 caracteres")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Nombre"), "Centro sintético");
    await user.type(screen.getByLabelText("Código"), "CC01");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText("Código duplicado en la empresa")).toBeInTheDocument();
    expect(screen.getByText("t-1")).toBeInTheDocument();
    expect(pointerToPath("#/lines/0/amount")).toBe("lines.0.amount");
  });

  it("autosaves drafts locally only for non-posting documents", async () => {
    const user = userEvent.setup();
    const { unmount } = renderDs(<Form />);
    await user.type(screen.getByLabelText("Nombre"), "Borrador");
    await waitFor(() => expect(window.localStorage.getItem("nexus:u-1:draft.demo")).toContain("Borrador"));
    unmount();
    const second = renderDs(<Form />);
    expect(screen.getByLabelText("Nombre")).toHaveValue("Borrador");
    second.unmount();
    window.localStorage.clear();
    renderDs(<Form posting />);
    await user.type(screen.getByLabelText("Nombre"), "Contable");
    expect(window.localStorage.getItem("nexus:u-1:draft.demo")).toBeNull();
  });
});
