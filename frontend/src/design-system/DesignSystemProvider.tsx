// Root provider for design-system consumers (owner P08): i18n + theme + tooltips + toasts.
import type { ReactNode } from "react";
import { I18nProvider, type Locale } from "@i18n/index";
import { ThemeProvider } from "./theme/ThemeProvider";
import { TooltipProvider } from "./components/Tooltip";
import { ToastProvider } from "./components/Toast";
import type { ThemeName } from "./tokens";

export function DesignSystemProvider({ children, locale = "es-CO", userId, theme }: { children: ReactNode; locale?: Locale; userId?: string | null; theme?: ThemeName }) {
  return (
    <I18nProvider locale={locale}>
      <ThemeProvider userId={userId} initialTheme={theme}>
        <TooltipProvider>
          <ToastProvider>{children}</ToastProvider>
        </TooltipProvider>
      </ThemeProvider>
    </I18nProvider>
  );
}
