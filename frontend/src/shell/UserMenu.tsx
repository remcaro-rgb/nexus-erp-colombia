// Profile menu (owner P08): avatar 28 px with initials, name ≥ md; "Perfil y preferencias", "Seguridad",
// density, "Cerrar sesión" → real OIDC logout (ShellContext.logout). Shows the signed-in user only.
import * as DM from "@radix-ui/react-dropdown-menu";
import { ChevronDown, LogOut, Shield, UserCog } from "lucide-react";
import { useNavigate } from "react-router";
import { useT } from "@i18n/index";
import { useTheme } from "@ds/theme/ThemeProvider";

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "")).toUpperCase() || "?";
}

export function UserMenu({ name, onLogout, profileRoute, securityRoute }: { name: string; onLogout: () => void; profileRoute?: string; securityRoute?: string }) {
  const t = useT();
  const nav = useNavigate();
  const { density, setDensity } = useTheme();
  const item = "flex min-h-8 cursor-pointer items-center gap-2 rounded-control px-3 py-1.5 text-sm text-body outline-none data-[highlighted]:bg-nav-hover";
  return (
    <DM.Root>
      <DM.Trigger asChild>
        <button type="button" aria-label={`${t("header.profile")}: ${name}`} className="inline-flex min-h-8 items-center gap-2 rounded-control px-1 hover:bg-nav-hover" data-nx-control="profile">
          <span aria-hidden="true" className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-brand-soft text-xs font-bold text-brand">
            {initials(name)}
          </span>
          <span aria-hidden="true" className="hidden max-w-40 truncate text-sm font-medium text-body md:inline">
            {name}
          </span>
          <ChevronDown aria-hidden="true" size={14} className="hidden text-muted md:inline" />
        </button>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content align="end" sideOffset={8} className="z-[50] min-w-56 rounded-card border border-line bg-surface p-1 shadow-xl">
          <DM.Label className="px-3 py-1.5 text-xs font-semibold text-muted">{name}</DM.Label>
          {profileRoute ? (
            <DM.Item className={item} onSelect={() => nav(profileRoute)}>
              <UserCog aria-hidden="true" size={14} />
              {t("user.profile")}
            </DM.Item>
          ) : null}
          {securityRoute ? (
            <DM.Item className={item} onSelect={() => nav(securityRoute)}>
              <Shield aria-hidden="true" size={14} />
              {t("user.security")}
            </DM.Item>
          ) : null}
          <DM.Separator className="my-1 h-px bg-line" />
          <DM.Label className="px-3 py-1 text-xs font-semibold text-muted">{t("user.density")}</DM.Label>
          <DM.RadioGroup value={density} onValueChange={(v) => setDensity(v as "comfortable" | "compact")}>
            <DM.RadioItem value="comfortable" className={item}>
              <DM.ItemIndicator>•</DM.ItemIndicator>
              {t("user.density.comfortable")}
            </DM.RadioItem>
            <DM.RadioItem value="compact" className={item}>
              <DM.ItemIndicator>•</DM.ItemIndicator>
              {t("user.density.compact")}
            </DM.RadioItem>
          </DM.RadioGroup>
          <DM.Separator className="my-1 h-px bg-line" />
          <DM.Item className={item} onSelect={onLogout}>
            <LogOut aria-hidden="true" size={14} />
            {t("user.logout")}
          </DM.Item>
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}
