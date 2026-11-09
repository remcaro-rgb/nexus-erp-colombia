// i18n provider (owner P08): es-CO default, en-US secondary, ICU messages. Hard-coded UI strings are
// forbidden in shell/design-system (lint test: src/i18n/__tests__/hardcoded-strings.unit.ts).
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { esCO, type MessageKey } from "./messages/es-CO";
import { enUS } from "./messages/en-US";
import { formatIcu, type MessageValues } from "./icu";

export type Locale = "es-CO" | "en-US";
export type { MessageKey, MessageValues };

const CATALOGS: Record<Locale, Record<MessageKey, string>> = { "es-CO": esCO, "en-US": enUS };

export function translate(locale: Locale, key: MessageKey, values?: MessageValues): string {
  const msg = CATALOGS[locale][key] ?? esCO[key] ?? key;
  return formatIcu(msg, values, locale);
}

export type TFunction = (key: MessageKey, values?: MessageValues) => string;

interface I18nValue {
  locale: Locale;
  t: TFunction;
}

const I18nContext = createContext<I18nValue>({ locale: "es-CO", t: (k, v) => translate("es-CO", k, v) });

export function I18nProvider({ locale = "es-CO", children }: { locale?: Locale; children: ReactNode }) {
  const t = useCallback<TFunction>((k, v) => translate(locale, k, v), [locale]);
  const value = useMemo(() => ({ locale, t }), [locale, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  return useContext(I18nContext);
}

export function useT(): TFunction {
  return useContext(I18nContext).t;
}

export { esCO, enUS };
