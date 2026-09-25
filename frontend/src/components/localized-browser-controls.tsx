"use client";

import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { isLanguageCode, type LanguageCode } from "@/i18n/client";

type Copy = {
  required: string; selectRequired: string; invalid: string; chooseFiles: string; noFile: string; files: string;
  clear: string; today: string; apply: string; previous: string; next: string;
  hour: string; minute: string; date: string; dateTime: string;
};

const copy: Record<LanguageCode, Copy> = {
  en: { required: "Please fill out this field.", selectRequired: "Please select an item from the list.", invalid: "Please enter a valid value.", chooseFiles: "Choose files", noFile: "No file selected", files: "files selected", clear: "Clear", today: "Today", apply: "Apply", previous: "Previous month", next: "Next month", hour: "Hour", minute: "Minute", date: "yyyy-mm-dd", dateTime: "yyyy-mm-dd --:--" },
  hi: { required: "कृपया यह फ़ील्ड भरें।", selectRequired: "कृपया सूची से एक विकल्प चुनें।", invalid: "कृपया मान्य मान दर्ज करें।", chooseFiles: "फ़ाइलें चुनें", noFile: "कोई फ़ाइल नहीं चुनी गई", files: "फ़ाइलें चुनी गईं", clear: "साफ़ करें", today: "आज", apply: "लागू करें", previous: "पिछला महीना", next: "अगला महीना", hour: "घंटा", minute: "मिनट", date: "वर्ष-माह-दिन", dateTime: "वर्ष-माह-दिन --:--" },
  kn: { required: "ದಯವಿಟ್ಟು ಈ ಕ್ಷೇತ್ರವನ್ನು ಭರ್ತಿ ಮಾಡಿ.", selectRequired: "ದಯವಿಟ್ಟು ಪಟ್ಟಿಯಿಂದ ಒಂದು ಆಯ್ಕೆಯನ್ನು ಆರಿಸಿ.", invalid: "ದಯವಿಟ್ಟು ಮಾನ್ಯವಾದ ಮೌಲ್ಯವನ್ನು ನಮೂದಿಸಿ.", chooseFiles: "ಕಡತಗಳನ್ನು ಆಯ್ಕೆಮಾಡಿ", noFile: "ಯಾವುದೇ ಕಡತವನ್ನು ಆಯ್ಕೆ ಮಾಡಿಲ್ಲ", files: "ಕಡತಗಳನ್ನು ಆಯ್ಕೆ ಮಾಡಲಾಗಿದೆ", clear: "ತೆರವುಗೊಳಿಸಿ", today: "ಇಂದು", apply: "ಅನ್ವಯಿಸಿ", previous: "ಹಿಂದಿನ ತಿಂಗಳು", next: "ಮುಂದಿನ ತಿಂಗಳು", hour: "ಗಂಟೆ", minute: "ನಿಮಿಷ", date: "ವರ್ಷ-ತಿಂಗಳು-ದಿನ", dateTime: "ವರ್ಷ-ತಿಂಗಳು-ದಿನ --:--" },
  ta: { required: "இந்தப் புலத்தை நிரப்பவும்.", selectRequired: "பட்டியலிலிருந்து ஓர் உருப்படியைத் தேர்ந்தெடுக்கவும்.", invalid: "செல்லுபடியாகும் மதிப்பை உள்ளிடவும்.", chooseFiles: "கோப்புகளைத் தேர்ந்தெடுக்கவும்", noFile: "கோப்பு தேர்ந்தெடுக்கப்படவில்லை", files: "கோப்புகள் தேர்ந்தெடுக்கப்பட்டன", clear: "அழிக்கவும்", today: "இன்று", apply: "பயன்படுத்து", previous: "முந்தைய மாதம்", next: "அடுத்த மாதம்", hour: "மணி", minute: "நிமிடம்", date: "ஆண்டு-மாதம்-நாள்", dateTime: "ஆண்டு-மாதம்-நாள் --:--" },
  te: { required: "దయచేసి ఈ క్షేత్రాన్ని పూరించండి.", selectRequired: "దయచేసి జాబితా నుండి ఒక అంశాన్ని ఎంచుకోండి.", invalid: "దయచేసి చెల్లుబాటు అయ్యే విలువను నమోదు చేయండి.", chooseFiles: "ఫైళ్లను ఎంచుకోండి", noFile: "ఫైల్ ఎంచుకోలేదు", files: "ఫైళ్లు ఎంచుకోబడ్డాయి", clear: "తొలగించండి", today: "ఈ రోజు", apply: "వర్తింపజేయండి", previous: "మునుపటి నెల", next: "తదుపరి నెల", hour: "గంట", minute: "నిమిషం", date: "సంవత్సరం-నెల-రోజు", dateTime: "సంవత్సరం-నెల-రోజు --:--" },
  mr: { required: "कृपया हे क्षेत्र भरा.", selectRequired: "कृपया सूचीमधून एक पर्याय निवडा.", invalid: "कृपया वैध मूल्य प्रविष्ट करा.", chooseFiles: "फायली निवडा", noFile: "कोणतीही फाइल निवडलेली नाही", files: "फायली निवडल्या", clear: "साफ करा", today: "आज", apply: "लागू करा", previous: "मागील महिना", next: "पुढील महिना", hour: "तास", minute: "मिनिट", date: "वर्ष-महिना-दिवस", dateTime: "वर्ष-महिना-दिवस --:--" },
};

const digits: Partial<Record<LanguageCode, string>> = { hi: "०१२३४५६७८९", mr: "०१२३४५६७८९", kn: "೦೧೨೩೪೫೬೭೮೯", ta: "௦௧௨௩௪௫௬௭௮௯", te: "౦౧౨౩౪౫౬౭౮౯" };
const locales: Record<LanguageCode, string> = { en: "en-IN", hi: "hi-IN", kn: "kn-IN", ta: "ta-IN", te: "te-IN", mr: "mr-IN" };
const metadata = new WeakMap<HTMLInputElement, { type: string; placeholder: string | null; readOnly: boolean }>();

function localDigits(value: string, language: LanguageCode) {
  const set = digits[language];
  return set ? value.replace(/\d/g, (digit) => set[Number(digit)]) : value;
}

function asciiDigits(value: string) {
  let output = value;
  for (const set of Object.values(digits)) output = output.replace(new RegExp(`[${set}]`, "g"), (digit) => String(set.indexOf(digit)));
  return output;
}

function setReactValue(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function closeCalendar() { document.querySelector(".localized-calendar")?.remove(); }

function openCalendar(input: HTMLInputElement, language: LanguageCode) {
  closeCalendar();
  const originalType = metadata.get(input)?.type || "date";
  const withTime = originalType === "datetime-local";
  const parsed = asciiDigits(input.value).match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
  const now = new Date();
  let selected = parsed ? new Date(Number(parsed[1]), Number(parsed[2]) - 1, Number(parsed[3])) : now;
  let view = new Date(selected.getFullYear(), selected.getMonth(), 1);
  let hour = parsed?.[4] || String(now.getHours()).padStart(2, "0");
  let minute = parsed?.[5] || String(now.getMinutes()).padStart(2, "0");
  const panel = document.createElement("div");
  panel.className = "localized-calendar";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", copy[language].date);
  document.body.append(panel);

  const commit = () => {
    const date = `${selected.getFullYear()}-${String(selected.getMonth() + 1).padStart(2, "0")}-${String(selected.getDate()).padStart(2, "0")}`;
    setReactValue(input, withTime ? `${date}T${hour}:${minute}` : date);
    closeCalendar();
    input.focus();
  };
  const render = () => {
    panel.replaceChildren();
    const header = document.createElement("div"); header.className = "localized-calendar-header";
    const previous = document.createElement("button"); previous.type = "button"; previous.textContent = "‹"; previous.title = copy[language].previous;
    const heading = document.createElement("strong"); heading.textContent = localDigits(new Intl.DateTimeFormat(locales[language], { month: "long", year: "numeric" }).format(view), language);
    const next = document.createElement("button"); next.type = "button"; next.textContent = "›"; next.title = copy[language].next;
    previous.onclick = () => { view = new Date(view.getFullYear(), view.getMonth() - 1, 1); render(); };
    next.onclick = () => { view = new Date(view.getFullYear(), view.getMonth() + 1, 1); render(); };
    header.append(previous, heading, next); panel.append(header);
    const grid = document.createElement("div"); grid.className = "localized-calendar-grid";
    for (let day = 0; day < 7; day++) { const label = document.createElement("span"); label.className = "weekday"; label.textContent = new Intl.DateTimeFormat(locales[language], { weekday: "narrow" }).format(new Date(2024, 0, 7 + day)); grid.append(label); }
    const offset = view.getDay(); const daysInMonth = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
    for (let blank = 0; blank < offset; blank++) grid.append(document.createElement("span"));
    for (let day = 1; day <= daysInMonth; day++) {
      const button = document.createElement("button"); button.type = "button"; button.textContent = localDigits(String(day), language);
      if (selected.getFullYear() === view.getFullYear() && selected.getMonth() === view.getMonth() && selected.getDate() === day) button.className = "selected";
      button.onclick = () => { selected = new Date(view.getFullYear(), view.getMonth(), day); if (withTime) render(); else commit(); };
      grid.append(button);
    }
    panel.append(grid);
    if (withTime) {
      const time = document.createElement("div"); time.className = "localized-calendar-time";
      const hourSelect = document.createElement("select"); hourSelect.title = copy[language].hour;
      const minuteSelect = document.createElement("select"); minuteSelect.title = copy[language].minute;
      for (let value = 0; value < 24; value++) { const option = document.createElement("option"); option.value = String(value).padStart(2, "0"); option.textContent = localDigits(option.value, language); option.selected = option.value === hour; hourSelect.append(option); }
      for (let value = 0; value < 60; value++) { const option = document.createElement("option"); option.value = String(value).padStart(2, "0"); option.textContent = localDigits(option.value, language); option.selected = option.value === minute; minuteSelect.append(option); }
      hourSelect.onchange = () => { hour = hourSelect.value; }; minuteSelect.onchange = () => { minute = minuteSelect.value; };
      const separator = document.createElement("span"); separator.textContent = ":"; time.append(hourSelect, separator, minuteSelect); panel.append(time);
    }
    const footer = document.createElement("div"); footer.className = "localized-calendar-footer";
    const clear = document.createElement("button"); clear.type = "button"; clear.textContent = copy[language].clear; clear.onclick = () => { setReactValue(input, ""); closeCalendar(); };
    const today = document.createElement("button"); today.type = "button"; today.textContent = copy[language].today; today.onclick = () => { selected = new Date(); view = new Date(selected.getFullYear(), selected.getMonth(), 1); if (withTime) render(); else commit(); };
    footer.append(clear, today);
    if (withTime) { const apply = document.createElement("button"); apply.type = "button"; apply.className = "apply"; apply.textContent = copy[language].apply; apply.onclick = commit; footer.append(apply); }
    panel.append(footer);
  };
  render();
  const rect = input.getBoundingClientRect();
  panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 330))}px`;
  panel.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - panel.offsetHeight - 8))}px`;
}

export function LocalizedBrowserControls() {
  const { i18n } = useTranslation();
  const language: LanguageCode = isLanguageCode(i18n.resolvedLanguage) ? i18n.resolvedLanguage : "en";
  useEffect(() => {
    const enhance = (root: ParentNode) => {
      const inputs = [...(root instanceof HTMLInputElement ? [root] : []), ...root.querySelectorAll<HTMLInputElement>("input")];
      for (const input of inputs) {
        input.autocomplete = "off";
        const type = input.getAttribute("type") || "text";
        if (!metadata.has(input)) metadata.set(input, { type, placeholder: input.getAttribute("placeholder"), readOnly: input.readOnly });
        const original = metadata.get(input)!;
        if (original.type === "file") {
          input.classList.add("localized-file-input");
          const label = input.closest("label");
          if (label) { label.classList.add("localized-file-field"); label.setAttribute("data-file-label", input.files?.length ? `${input.files.length} ${copy[language].files}` : `${copy[language].chooseFiles} · ${copy[language].noFile}`); }
        }
        if (["date", "datetime-local"].includes(original.type)) {
          if (language === "en") { input.type = original.type; input.readOnly = original.readOnly; if (original.placeholder === null) input.removeAttribute("placeholder"); else input.placeholder = original.placeholder; }
          else { input.type = "text"; input.readOnly = true; input.classList.add("localized-date-input"); input.placeholder = original.type === "date" ? copy[language].date : copy[language].dateTime; if (input.value) input.value = localDigits(asciiDigits(input.value), language); }
        }
      }
      const forms = [...(root instanceof HTMLFormElement ? [root] : []), ...root.querySelectorAll<HTMLFormElement>("form")];
      for (const form of forms) form.autocomplete = "off";
      const ownField = root instanceof HTMLInputElement || root instanceof HTMLSelectElement || root instanceof HTMLTextAreaElement ? [root] : [];
      const fields = [...ownField, ...root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input, select, textarea")];
      for (const field of fields) field.setCustomValidity("");
    };
    enhance(document);
    const observer = new MutationObserver((items) => { for (const item of items) for (const node of item.addedNodes) if (node instanceof Element) enhance(node); });
    observer.observe(document.body, { childList: true, subtree: true });
    const onClick = (event: Event) => { const input = event.target instanceof HTMLInputElement ? event.target : null; if (input?.classList.contains("localized-date-input")) { event.preventDefault(); openCalendar(input, language); } else if (!(event.target instanceof Element) || !event.target.closest(".localized-calendar")) closeCalendar(); };
    const onInvalid = (event: Event) => { const field = event.target; if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement)) return; field.setCustomValidity(""); if (field.validity.valid) return; field.setCustomValidity(field.validity.valueMissing ? field instanceof HTMLSelectElement ? copy[language].selectRequired : copy[language].required : copy[language].invalid); };
    const onInput = (event: Event) => { const field = event.target; if (field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement) field.setCustomValidity(""); if (field instanceof HTMLInputElement && field.type === "file") { const label = field.closest("label"); if (label) label.setAttribute("data-file-label", field.files?.length ? `${field.files.length} ${copy[language].files}` : `${copy[language].chooseFiles} · ${copy[language].noFile}`); } };
    const onChange = (event: Event) => { const field = event.target; if (field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement) field.setCustomValidity(""); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") closeCalendar(); };
    document.addEventListener("click", onClick, true); document.addEventListener("invalid", onInvalid, true); document.addEventListener("input", onInput, true); document.addEventListener("change", onChange, true); document.addEventListener("keydown", onKey, true);
    return () => { observer.disconnect(); closeCalendar(); document.removeEventListener("click", onClick, true); document.removeEventListener("invalid", onInvalid, true); document.removeEventListener("input", onInput, true); document.removeEventListener("change", onChange, true); document.removeEventListener("keydown", onKey, true); };
  }, [language]);
  return null;
}
