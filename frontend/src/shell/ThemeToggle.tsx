// Light/dark toggle (owner P08): sun/moon, aria-pressed = dark mode on.
import { Moon, Sun } from "lucide-react";
import { useT } from "@i18n/index";
import { useTheme } from "@ds/theme/ThemeProvider";

export function ThemeToggle() {
  const t = useT();
  const { theme, toggleTheme } = useTheme();
  const dark = theme === "dark";
  return (
    <button
      type="button"
      aria-pressed={dark}
      aria-label={dark ? t("header.theme.toLight") : t("header.theme.toDark")}
      title={dark ? t("header.theme.toLight") : t("header.theme.toDark")}
      onClick={toggleTheme}
      className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-control p-2 text-secondary hover:bg-nav-hover"
      data-nx-control="theme"
    >
      {dark ? <Sun aria-hidden="true" size={16} /> : <Moon aria-hidden="true" size={16} />}
    </button>
  );
}
