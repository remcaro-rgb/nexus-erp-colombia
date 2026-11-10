import { Link } from "react-router";
import { useT } from "@i18n/index";
import { EmptyState } from "@ds/components/Feedback";

export function NotFound({ home = "/m/inicio" }: { home?: string }) {
  const t = useT();
  return (
    <div className="nx-card">
      <EmptyState
        title={t("app.notFound.title")}
        body={t("app.notFound.body")}
        action={
          <Link to={home} className="text-sm font-semibold text-brand hover:underline">
            {t("app.notFound.action")}
          </Link>
        }
      />
    </div>
  );
}
