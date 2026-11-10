// Internal top bar (owner P08, UI-CONTRACT §4): brand · global search · company · period · spacer · AI toggle ·
// notifications · theme · profile. Narrow widths: search → icon (< 1280); company+period → context button (< 1024).
import { Menu, Search, Sparkles } from "lucide-react";
import { useT } from "@i18n/index";
import { cn } from "@lib/cn";
import { Brand } from "./Brand";
import { CompanySelect, ContextButton, PeriodSelect } from "./ContextSwitcher";
import { NotificationsPopover } from "./NotificationsPopover";
import { ThemeToggle } from "./ThemeToggle";
import { TopBar } from "./TopBar";
import { UserMenu } from "./UserMenu";
import { useShell } from "./ShellContext";
import type { Breakpoint } from "./useBreakpoint";

export const AI_PANEL_ID = "nx-ai-panel";

export function Header({ bp, onOpenSearch, onOpenNav, navOpen }: { bp: Breakpoint; onOpenSearch: () => void; onOpenNav: () => void; navOpen: boolean }) {
  const t = useT();
  const shell = useShell();
  const name = shell.context.user?.display_name ?? shell.me.display_name ?? shell.me.id;
  const left = (
    <>
      {bp === "sm" ? (
        <button
          type="button"
          aria-label={t("header.menu")}
          aria-expanded={navOpen}
          aria-controls="nx-sidebar"
          onClick={onOpenNav}
          className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-control text-secondary hover:bg-nav-hover"
        >
          <Menu aria-hidden="true" size={18} />
        </button>
      ) : null}
      <Brand name={shell.context.brand?.name || "NexusERP"} logoPath={shell.context.brand?.logo_path} to="/m/inicio" />
      {bp === "xl" ? (
        <button
          type="button"
          onClick={onOpenSearch}
          aria-keyshortcuts="Control+K Meta+K /"
          aria-haspopup="dialog"
          data-nx-control="search"
          className={cn(
            "ml-2 inline-flex min-h-8 items-center gap-2 rounded-control bg-input px-3 py-1.5 text-left text-xs text-muted hover:text-secondary",
            "w-[var(--nx-search-w-compact)] 2xl:w-[var(--nx-search-w)] min-[1440px]:w-[var(--nx-search-w)]",
          )}
        >
          <Search aria-hidden="true" size={14} />
          <span className="truncate">{t("header.search")}</span>
        </button>
      ) : (
        <button
          type="button"
          onClick={onOpenSearch}
          aria-label={t("header.searchShort")}
          aria-keyshortcuts="Control+K Meta+K /"
          aria-haspopup="dialog"
          data-nx-control="search"
          className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-control text-secondary hover:bg-nav-hover"
        >
          <Search aria-hidden="true" size={16} />
        </button>
      )}
      {bp === "xl" || bp === "lg" ? (
        <div className="flex items-center gap-2">
          <CompanySelect />
          <PeriodSelect />
        </div>
      ) : (
        <ContextButton />
      )}
    </>
  );
  const right = (
    <>
      <button
        type="button"
        aria-expanded={shell.aiOpen}
        aria-controls={AI_PANEL_ID}
        onClick={() => shell.setAiOpen(!shell.aiOpen)}
        data-nx-control="ai-toggle"
        className={cn(
          "inline-flex min-h-8 items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold",
          shell.aiOpen ? "border-brand bg-brand text-white dark:text-slate-950" : "border-brand-soft-line bg-brand-soft text-brand hover:bg-indigo-100 dark:hover:bg-indigo-950",
        )}
      >
        <Sparkles aria-hidden="true" size={14} />
        <span className={bp === "sm" ? "nx-sr-only" : ""}>{t("header.ai")}</span>
      </button>
      <NotificationsPopover />
      <ThemeToggle />
      <UserMenu name={name} onLogout={shell.logout} profileRoute="/m/administracion/perfil" securityRoute="/m/administracion/seguridad" />
    </>
  );
  return <TopBar left={left} right={right} />;
}
