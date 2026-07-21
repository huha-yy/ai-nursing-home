import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { en } from './en';
import { zh } from './zh';

export type Locale = 'en' | 'zh';

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}

const STORAGE_KEY = 'gbrain_admin_locale';

const I18nContext = createContext<I18nContextValue>({
  locale: 'zh',
  setLocale: () => {},
  t: (key) => key,
});

const messages: Record<Locale, Record<string, string>> = { en, zh };

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const stored = typeof window !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null;
    return stored === 'en' || stored === 'zh' ? stored : 'zh';
  });

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // localStorage unavailable (private browsing, etc.)
    }
  }, []);

  const t = useCallback(
    (key: string, params?: Record<string, string | number>): string => {
      const msg = messages[locale][key];
      if (msg === undefined) {
        // Fallback to English, then key itself
        const fallback = messages.en[key];
        if (fallback === undefined) return key;
        return applyParams(fallback, params);
      }
      return applyParams(msg, params);
    },
    [locale],
  );

  return (
    <I18nContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}

export function useT() {
  return useI18n().t;
}

export function useLocale() {
  return useI18n().locale;
}

export function useSetLocale() {
  return useI18n().setLocale;
}

/**
 * Locale-aware relative time formatting.
 * Returns strings like "3分钟前" (zh) or "3 min ago" (en).
 */
export function timeAgo(date: string | Date, locale: Locale): string {
  const diff = Date.now() - new Date(date).getTime();
  const abs = Math.abs(diff);

  if (abs < 60000) {
    const s = Math.floor(abs / 1000);
    if (locale === 'zh') return `${s}秒前`;
    return `${s}s ago`;
  }
  if (abs < 3600000) {
    const m = Math.floor(abs / 60000);
    if (locale === 'zh') return `${m}分钟前`;
    return `${m} min ago`;
  }
  if (abs < 86400000) {
    const h = Math.floor(abs / 3600000);
    if (locale === 'zh') return `${h}小时前`;
    return `${h}h ago`;
  }
  const d = Math.floor(abs / 86400000);
  if (locale === 'zh') return `${d}天前`;
  return `${d}d ago`;
}

// Helpers

function applyParams(msg: string, params?: Record<string, string | number>): string {
  if (!params) return msg;
  let result = msg;
  for (const [k, v] of Object.entries(params)) {
    result = result.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v));
  }
  return result;
}
