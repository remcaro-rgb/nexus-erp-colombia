// TEMPLATE document page (owner P08): DocumentLayout + useEntityForm + accounting preview from :simulate.
import { z } from "zod";
import { useT } from "@i18n/index";
import { DocumentLayout } from "@ds/document";
import { PostingBadge } from "@ds/components/Badge";
import { Field, Input } from "@ds/components/Field";
import { useEntityForm } from "@ds/forms";
import { useShell } from "../ShellContext";
import type { PageComponent } from "../registry";

const schema = z.object({ reference: z.string().min(1) });

const ExampleDocumentPage: PageComponent = ({ module }) => {
  const t = useT();
  const shell = useShell();
  const { form, submit } = useEntityForm({
    schema,
    defaultValues: { reference: "" },
    draftKey: `${module.id}.document`,
    userId: shell.userId,
    onSubmit: async (values) => {
      await shell.api.post(`/api/v1/${module.id}/documents`, { body: values, headers: shell.companyId ? { "X-Company-Id": shell.companyId } : undefined });
    },
  });
  return (
    <DocumentLayout
      title={t("template.document")}
      state={<PostingBadge status="draft" />}
      header={
        <Field label={t("tree.code")} error={form.formState.errors.reference?.message}>
          {(p) => <Input id={p.id} aria-describedby={p.describedBy} aria-invalid={p.invalid || undefined} {...form.register("reference")} />}
        </Field>
      }
      lines={null}
      totals={null}
      panels={{ accounting: null, audit: null }}
      actions={[{ id: "save", label: t("ds.save"), onClick: () => void submit(), primary: true }]}
    />
  );
};

export default ExampleDocumentPage;
