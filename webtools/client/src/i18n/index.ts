/**
 * i18n initialization (i18next + react-i18next)
 * ────────────────────────────────────────────────────────────────────────────
 * Language packs are **bundled**, zero network requests at runtime (matching the engineering rule
 * "resources strictly offline").
 *
 * Languages:
 *   · zh-CN  Simplified Chinese
 *   · en-US  English
 *   · en-XA  pseudo-localization (**development/testing only**, see pseudo.ts) — used for layout stress testing
 *
 * Default language on first visit (decided 2026-11-19, planning doc §8-③):
 *   **follow the browser**, fall back to English when no match (open-source audience first).
 *   Browser language starting with zh → zh-CN; everything else → en-US.
 *   Once the user switches manually, it is written to localStorage and the user's choice always wins afterwards
 *   (no longer drifting with the browser).
 *
 * Persistence: localStorage["agentgeex.locale"]
 *
 * ★ Namespaces (2026-11-18): language packs are split by functional domain under `locales/<locale>/`,
 *   and merged into one tree in the entry object here — callers still write `t("header.send")` unchanged.
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import zhCN from "./locales/zh-CN";
import enUS from "./locales/en-US";
import { pseudoize } from "./pseudo";

export const LOCALE_STORAGE_KEY = "agentgeex.locale";

/** Product languages (user visible) */
export type LocaleKey = "zh-CN" | "en-US";
/** Debug language: pseudo-localization. Does not appear in the normal language menu. */
export type DebugLocaleKey = "en-XA";
export type AnyLocaleKey = LocaleKey | DebugLocaleKey;

export const SUPPORTED_LOCALES: { key: LocaleKey; label: string; short: string }[] = [
  { key: "zh-CN", label: "简体中文", short: "中" },
  { key: "en-US", label: "English", short: "EN" },
];

/** Fallback language: **English** (open-source audience). Used when the browser language is unrecognized. */
export const FALLBACK_LOCALE: LocaleKey = "en-US";

/**
 * First visit: guess from the browser language.
 * Only the binary "Chinese family vs others" — no zh-TW/zh-HK subdivision (there is only one Simplified Chinese pack;
 * splitting it out would leave nothing to put in, and would make Traditional Chinese users see Simplified and assume a bug).
 */
export function detectBrowserLocale(): LocaleKey {
  try {
    const langs = navigator.languages?.length ? navigator.languages : [navigator.language];
    for (const raw of langs) {
      if (!raw) continue;
      if (raw.toLowerCase().startsWith("zh")) return "zh-CN";
    }
  } catch {
    /* No navigator (SSR / extreme environments) → use the fallback */
  }
  return FALLBACK_LOCALE;
}

/** Pseudo-localization only enabled during local development/acceptance: `?lang=en-XA` or change localStorage */
const PSEUDO_ENABLED = true;

/** Read the effective language: saved > browser language > fallback English */
export function resolveInitialLocale(): AnyLocaleKey {
  try {
    const v = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (v === "zh-CN" || v === "en-US") return v;
    if (v === "en-XA" && PSEUDO_ENABLED) return v;
  } catch {
    /* Ignore errors in SSR / private mode */
  }
  return detectBrowserLocale();
}

/** Sync <html lang> (accessibility + search engines) */
export function applyHtmlLang(locale: string) {
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("lang", locale);
  }
}

const resources = {
  "zh-CN": { translation: zhCN },
  "en-US": { translation: enUS },
  /* Pseudo text is generated from the Chinese pack on the same tree — adding a key needs no third copy maintained. */
  "en-XA": { translation: pseudoize(zhCN) as unknown as typeof zhCN },
};

i18n.use(initReactI18next).init({
  resources,
  lng: resolveInitialLocale(),
  /* When a key is missing in the target language, fall back to English (not Chinese) — consistent with the fallback language */
  fallbackLng: FALLBACK_LOCALE,
  interpolation: { escapeValue: false },
  returnNull: false,
});

applyHtmlLang(i18n.language);

/** Switch language: write i18n + persist + update <html lang> */
export function changeLocale(locale: AnyLocaleKey) {
  void i18n.changeLanguage(locale);
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    /* ignore */
  }
  applyHtmlLang(locale);
}

export default i18n;
