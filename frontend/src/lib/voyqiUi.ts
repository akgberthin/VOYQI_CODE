/* The platform's shared theme + language library (the same one the
   Marketing Command Center HTML ships), loaded from /voyqi-ui.js.
   It translates on-screen text by exact match from its catalogs, keeps
   watching the page as React re-renders, sets <html lang/dir>, and stores
   the choices under voyqi.locale / voyqi.theme. */

export type Locale = { code: string; english: string; native: string; region: "europe" | "africa" | "asia"; lang: string; dir: "ltr" | "rtl"; reviewed: boolean; active: boolean };
export type Theme = "light" | "dark" | "system";

type Lib = {
  LOCALES: Locale[];
  REGION_LABEL: Record<string, string>;
  init: (o?: { translate?: boolean; mount?: unknown }) => void;
  getLocale: () => string;
  setLocale: (code: string) => string;
  subscribe: (cb: () => void) => () => void;
  getCatalog: (code: string) => Record<string, string>;
  getThemePref: () => Theme;
  setThemePref: (t: Theme) => void;
  subscribeTheme: (cb: () => void) => () => void;
};

declare global {
  interface Window { VoyqiUI?: Lib }
}

let loading: Promise<Lib | null> | null = null;

/** Loads and starts the library once; resolves to null if it can't load (the console stays in English). */
export function loadVoyqiUi(): Promise<Lib | null> {
  if (typeof window === "undefined") return Promise.resolve(null);
  if (window.VoyqiUI) return Promise.resolve(window.VoyqiUI);
  loading ||= new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = "/voyqi-ui.js";
    s.async = true;
    s.onload = () => {
      try { window.VoyqiUI!.init({ translate: true }); resolve(window.VoyqiUI!); } catch { resolve(null); }
    };
    s.onerror = () => resolve(null);
    document.head.appendChild(s);
  });
  return loading;
}

/** Languages offered in the picker — the active ones that have a catalog, by region (same rule as the HTML). */
export function pickerGroups(lib: Lib): [string, Locale[]][] {
  return (["europe", "africa", "asia"] as const)
    .map((r) => [lib.REGION_LABEL[r] || r, lib.LOCALES.filter((l) => l.active && l.region === r && (l.code === "en" || Object.keys(lib.getCatalog(l.code) || {}).length > 0))] as [string, Locale[]])
    .filter(([, ls]) => ls.length > 0);
}
