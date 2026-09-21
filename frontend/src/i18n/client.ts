"use client";

import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "@/i18n/locales/en.json";
import hi from "@/i18n/locales/hi.json";
import kn from "@/i18n/locales/kn.json";
import mr from "@/i18n/locales/mr.json";
import ta from "@/i18n/locales/ta.json";
import te from "@/i18n/locales/te.json";

export const LANGUAGE_STORAGE_KEY = "admiezo-language";
export const supportedLanguages = [
  { code: "en", shortCode: "EN", label: "English" },
  { code: "hi", shortCode: "HI", label: "हिन्दी" },
  { code: "kn", shortCode: "KN", label: "ಕನ್ನಡ" },
  { code: "ta", shortCode: "TA", label: "தமிழ்" },
  { code: "te", shortCode: "TE", label: "తెలుగు" },
  { code: "mr", shortCode: "MR", label: "मराठी" },
] as const;

export type LanguageCode = (typeof supportedLanguages)[number]["code"];
export const localeResources = { en, hi, kn, ta, te, mr };

export function isLanguageCode(value: string | null | undefined): value is LanguageCode {
  return supportedLanguages.some((language) => language.code === value);
}

if (!i18n.isInitialized) {
  void i18n.use(initReactI18next).init({
    resources: Object.fromEntries(Object.entries(localeResources).map(([language, translation]) => [language, { translation }])),
    lng: "en",
    fallbackLng: "en",
    supportedLngs: supportedLanguages.map((language) => language.code),
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
}

export default i18n;
