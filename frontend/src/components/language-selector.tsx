"use client";

import { Check, ChevronDown, Globe2 } from "lucide-react";
import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n, {
  isLanguageCode,
  LANGUAGE_STORAGE_KEY,
  LanguageCode,
  supportedLanguages,
} from "@/i18n/client";

export function LanguageSelector() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const currentCode = isLanguageCode(i18n.resolvedLanguage) ? i18n.resolvedLanguage : "en";
  const selectedIndex = supportedLanguages.findIndex((language) => language.code === currentCode);

  useEffect(() => {
    const storedLanguage = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (isLanguageCode(storedLanguage) && storedLanguage !== i18n.resolvedLanguage) {
      void i18n.changeLanguage(storedLanguage);
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = currentCode;
  }, [currentCode]);

  useEffect(() => {
    if (!open) return;
    window.requestAnimationFrame(() => optionRefs.current[activeIndex]?.focus());
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [activeIndex, open]);

  function selectLanguage(code: LanguageCode) {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, code);
    void i18n.changeLanguage(code);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function openAt(index: number) {
    setActiveIndex(index);
    setOpen(true);
  }

  function onTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown") { event.preventDefault(); openAt((selectedIndex + 1) % supportedLanguages.length); }
    if (event.key === "ArrowUp") { event.preventDefault(); openAt((selectedIndex - 1 + supportedLanguages.length) % supportedLanguages.length); }
  }

  function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") { event.preventDefault(); setOpen(false); triggerRef.current?.focus(); return; }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? supportedLanguages.length - 1 : event.key === "ArrowDown" ? (activeIndex + 1) % supportedLanguages.length : (activeIndex - 1 + supportedLanguages.length) % supportedLanguages.length;
    setActiveIndex(nextIndex);
    optionRefs.current[nextIndex]?.focus();
  }

  const selectedLanguage = supportedLanguages[selectedIndex];
  return <div className="language-selector" ref={rootRef}>
    <button ref={triggerRef} className="language-trigger" type="button" aria-haspopup="menu" aria-expanded={open} aria-controls="language-menu" aria-label={t("languageSelector.label")} title={t("languageSelector.label")} onClick={() => { setActiveIndex(selectedIndex); setOpen((value) => !value); }} onKeyDown={onTriggerKeyDown}>
      <Globe2 /><span>Language</span><span className="language-code">{selectedLanguage.shortCode}</span><ChevronDown className={open ? "open" : ""} />
    </button>
    {open && <div id="language-menu" className="language-menu" role="menu" aria-label={t("languageSelector.label")} onKeyDown={onMenuKeyDown}>
      {supportedLanguages.map((language, index) => <button key={language.code} ref={(element) => { optionRefs.current[index] = element; }} type="button" role="menuitemradio" aria-checked={language.code === currentCode} tabIndex={index === activeIndex ? 0 : -1} onFocus={() => setActiveIndex(index)} onClick={() => selectLanguage(language.code)}>
        <span><strong>{language.shortCode}</strong>{language.label}</span>{language.code === currentCode && <Check />}
      </button>)}
    </div>}
  </div>;
}
