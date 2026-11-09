// Theme + density + token scope (owner P08). Class strategy: 'dark' on <html>; default = OS preference;
// persisted per user (tokens.json color.theme_switch). Every shell variant (internal, portal) renders inside
// <TokenScope>, which applies the SAME variables from tokens.json — portals cannot diverge from the internal shell.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { cssVariables, tokens, tokensFingerprint, type ThemeName } from "../tokens";
import { prefKey, readPref, writePref } from "@lib/storage";

export type Density = "comfortable" | "compact";

interface ThemeValue {
  theme: ThemeName;
  setTheme: (t: ThemeName) => void;
  toggleTheme: () => void;
  density: Density;
  setDensity: (d: Density) => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

function osTheme(): ThemeName {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function ThemeProvider({ userId, children, initialTheme }: { userId?: string | null; children: ReactNode; initialTheme?: ThemeName }) {
  const themeKey = prefKey(userId, "theme");
  const densityKey = prefKey(userId, "density");
  const [theme, setThemeState] = useState<ThemeName>(() => initialTheme ?? readPref<ThemeName | null>(themeKey, null) ?? osTheme());
  // tokens.json: compact density is the reference default (text 12–14 px).
  const [density, setDensityState] = useState<Density>(() => readPref<Density>(densityKey, "compact"));

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.classList.toggle("light", theme === "light");
    root.dataset.density = density;
  }, [theme, density]);

  const setTheme = useCallback(
    (t: ThemeName) => {
      setThemeState(t);
      writePref(themeKey, t);
    },
    [themeKey],
  );
  const setDensity = useCallback(
    (d: Density) => {
      setDensityState(d);
      writePref(densityKey, d);
    },
    [densityKey],
  );
  const value = useMemo<ThemeValue>(
    () => ({ theme, setTheme, toggleTheme: () => setTheme(theme === "dark" ? "light" : "dark"), density, setDensity }),
    [theme, setTheme, density, setDensity],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const v = useContext(ThemeContext);
  if (!v) throw new Error("useTheme must be used inside <ThemeProvider>");
  return v;
}

/** Applies the token variables of the current theme to its subtree; exposes the fingerprint for tests/audits. */
export function TokenScope({ children, className, variant }: { children: ReactNode; className?: string; variant: "internal" | "portal" }) {
  const { theme, density } = useTheme();
  const vars = useMemo(() => cssVariables(tokens, theme), [theme]);
  const fp = useMemo(() => tokensFingerprint(vars), [vars]);
  return (
    <div
      className={className}
      style={vars as React.CSSProperties}
      data-nx-shell={variant}
      data-nx-theme={theme}
      data-nx-density={density}
      data-nx-tokens={fp}
    >
      {children}
    </div>
  );
}
