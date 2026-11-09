// Brand (owner P08): 32 px indigo tile (radius 12) with the lucide `layers` icon + "Nexus" / "ERP".
// Placeholder branding, configurable through ERP_BRAND_NAME / ERP_BRAND_LOGO_PATH (served in /ui/context brand).
import { Layers } from "lucide-react";
import { Link } from "react-router";
import { useT } from "@i18n/index";

export function Brand({ name, logoPath, to }: { name: string; logoPath?: string | null; to: string }) {
  const t = useT();
  // tokens.brand.name_parts: the placeholder "NexusERP" renders as "Nexus" (text.primary) + "ERP" (brand.primary).
  const split = /^(.+?)(ERP)$/.exec(name);
  return (
    <Link to={to} aria-label={t("header.brandHome", { brand: name })} className="flex shrink-0 items-center gap-2 rounded-control">
      <span aria-hidden="true" className="inline-flex h-8 w-8 items-center justify-center rounded-[12px] bg-[var(--nx-brand-tile-bg)] shadow-[var(--nx-brand-tile-shadow)]">
        {logoPath ? <img src={logoPath} alt="" className="h-4 w-4" /> : <Layers size={16} color="#ffffff" />}
      </span>
      <span aria-hidden="true" className="hidden text-base font-bold sm:inline">
        {split ? (
          <>
            <span className="text-fg">{split[1]}</span>
            <span className="text-brand">{split[2]}</span>
          </>
        ) : (
          <span className="text-fg">{name}</span>
        )}
      </span>
    </Link>
  );
}
