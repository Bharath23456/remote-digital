"use client";

import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import generated from "@/i18n/generated-ui.json";
import { isLanguageCode, LanguageCode, localeResources } from "@/i18n/client";
import { recordLocalizations } from "@/i18n/record-localizations";
import { supplementalLocalizations } from "@/i18n/supplemental-localizations";
import { domainLocalizations } from "@/i18n/domain-localizations";
import { coverageLocalizations } from "@/i18n/coverage-localizations";
import { LocalizedBrowserControls } from "@/components/localized-browser-controls";

type Catalogue = Record<LanguageCode, Record<string, string>>;

const catalogue = generated as Catalogue;
const runtimeTerms: Catalogue = {
  en: {},
  hi: { Transfers: "स्थानांतरण", Scans: "स्कैन", Reconciliations: "मिलान", Events: "घटनाएँ", Removed: "हटाए गए", Evaluating: "मूल्यांकनाधीन", Exception: "अपवाद", Reconciled: "मिलान किया गया", Bundles: "बंडल", Alerts: "चेतावनियाँ", Scripts: "उत्तरपुस्तिकाएँ", Dispatches: "प्रेषण", Packets: "पैकेट", Open: "खुला", Acknowledged: "स्वीकार किया गया", Registered: "पंजीकृत", Received: "प्राप्त", Assigned: "आवंटित", Stored: "संग्रहीत", Submitted: "जमा किया गया", Archived: "अभिलेखित", Requested: "अनुरोधित", Authorized: "अधिकृत", "In Transit": "परिवहन में", Rejected: "अस्वीकृत", Valid: "वैध", Invalid: "अमान्य", Unknown: "अज्ञात", None: "कोई नहीं", Online: "ऑनलाइन", Offline: "ऑफ़लाइन", Queued: "कतार में", Running: "चल रहा है", Failed: "विफल", Returned: "लौटाया गया", Reviewed: "समीक्षित", Cleared: "समाधान किया गया", Approved: "स्वीकृत", Reopened: "पुनः खोला गया", Resolved: "समाधान हुआ", Processing: "प्रसंस्करण", Completed: "पूर्ण", Pending: "लंबित", Active: "सक्रिय", Inactive: "निष्क्रिय", Ready: "तैयार", Attention: "ध्यान आवश्यक", Cloud: "क्लाउड", Core: "मुख्य", Healthy: "स्वस्थ" },
  kn: { Transfers: "ವರ್ಗಾವಣೆಗಳು", Scans: "ಸ್ಕ್ಯಾನ್‌ಗಳು", Reconciliations: "ಸಮನ್ವಯಗಳು", Events: "ಘಟನೆಗಳು", Removed: "ತೆಗೆದುಹಾಕಲಾಗಿದೆ", Evaluating: "ಮೌಲ್ಯಮಾಪನದಲ್ಲಿದೆ", Exception: "ವಿಶೇಷ ಪ್ರಕರಣ", Reconciled: "ಸಮನ್ವಯಗೊಂಡಿದೆ", Bundles: "ಕಟ್ಟುಗಳು", Alerts: "ಎಚ್ಚರಿಕೆಗಳು", Scripts: "ಉತ್ತರಪತ್ರಿಕೆಗಳು", Dispatches: "ರವಾನೆಗಳು", Packets: "ಪ್ಯಾಕೆಟ್‌ಗಳು", Open: "ತೆರೆದಿದೆ", Acknowledged: "ದೃಢೀಕರಿಸಲಾಗಿದೆ", Registered: "ನೋಂದಾಯಿಸಲಾಗಿದೆ", Received: "ಸ್ವೀಕರಿಸಲಾಗಿದೆ", Assigned: "ನಿಯೋಜಿಸಲಾಗಿದೆ", Stored: "ಸಂಗ್ರಹಿಸಲಾಗಿದೆ", Submitted: "ಸಲ್ಲಿಸಲಾಗಿದೆ", Archived: "ಆರ್ಕೈವ್ ಮಾಡಲಾಗಿದೆ", Requested: "ವಿನಂತಿಸಲಾಗಿದೆ", Authorized: "ಅಧಿಕೃತವಾಗಿದೆ", "In Transit": "ಸಾಗಣೆಯಲ್ಲಿದೆ", Rejected: "ತಿರಸ್ಕರಿಸಲಾಗಿದೆ", Valid: "ಮಾನ್ಯ", Invalid: "ಅಮಾನ್ಯ", Unknown: "ತಿಳಿದಿಲ್ಲ", None: "ಯಾವುದೂ ಇಲ್ಲ", Online: "ಆನ್‌ಲೈನ್", Offline: "ಆಫ್‌ಲೈನ್", Queued: "ಸರದಿಯಲ್ಲಿದೆ", Running: "ಚಾಲನೆಯಲ್ಲಿದೆ", Failed: "ವಿಫಲವಾಗಿದೆ", Returned: "ಹಿಂತಿರುಗಿಸಲಾಗಿದೆ", Reviewed: "ಪರಿಶೀಲಿಸಲಾಗಿದೆ", Cleared: "ತೆರವುಗೊಳಿಸಲಾಗಿದೆ", Approved: "ಅನುಮೋದಿಸಲಾಗಿದೆ", Reopened: "ಮರುತೆರೆಯಲಾಗಿದೆ", Resolved: "ಪರಿಹರಿಸಲಾಗಿದೆ", Processing: "ಸಂಸ್ಕರಣೆಯಲ್ಲಿದೆ", Completed: "ಪೂರ್ಣಗೊಂಡಿದೆ", Pending: "ಬಾಕಿಯಿದೆ", Active: "ಸಕ್ರಿಯ", Inactive: "ನಿಷ್ಕ್ರಿಯ", Ready: "ಸಿದ್ಧ", Attention: "ಗಮನ ಅಗತ್ಯ", Cloud: "ಕ್ಲೌಡ್", Core: "ಮುಖ್ಯ", Healthy: "ಆರೋಗ್ಯಕರ" },
  ta: { Transfers: "இடமாற்றங்கள்", Scans: "வருடல்கள்", Reconciliations: "ஒத்திசைவுகள்", Events: "நிகழ்வுகள்", Removed: "நீக்கப்பட்டவை", Evaluating: "மதிப்பீட்டில் உள்ளது", Exception: "விதிவிலக்கு", Reconciled: "ஒத்திசைக்கப்பட்டது", Bundles: "தொகுப்புகள்", Alerts: "எச்சரிக்கைகள்", Scripts: "விடைத்தாள்கள்", Dispatches: "அனுப்புகைகள்", Packets: "பொட்டலங்கள்", Open: "திறந்தது", Acknowledged: "ஏற்கப்பட்டது", Registered: "பதிவுசெய்யப்பட்டது", Received: "பெறப்பட்டது", Assigned: "ஒதுக்கப்பட்டது", Stored: "சேமிக்கப்பட்டது", Submitted: "சமர்ப்பிக்கப்பட்டது", Archived: "காப்பகப்படுத்தப்பட்டது", Requested: "கோரப்பட்டது", Authorized: "அங்கீகரிக்கப்பட்டது", "In Transit": "போக்குவரத்தில்", Rejected: "நிராகரிக்கப்பட்டது", Valid: "செல்லுபடியாகும்", Invalid: "செல்லாது", Unknown: "தெரியாதது", None: "எதுவுமில்லை", Online: "இணைப்பில்", Offline: "இணைப்பின்றி", Queued: "வரிசையில்", Running: "இயங்குகிறது", Failed: "தோல்வியடைந்தது", Returned: "திருப்பப்பட்டது", Reviewed: "மதிப்பாய்வு செய்யப்பட்டது", Cleared: "தீர்க்கப்பட்டது", Approved: "ஒப்புதல் அளிக்கப்பட்டது", Reopened: "மீண்டும் திறக்கப்பட்டது", Resolved: "தீர்க்கப்பட்டது", Processing: "செயலாக்கத்தில்", Completed: "முடிந்தது", Pending: "நிலுவையில்", Active: "செயலில்", Inactive: "செயலற்றது", Ready: "தயார்", Attention: "கவனம் தேவை", Cloud: "மேகம்", Core: "மையம்", Healthy: "நலமாக உள்ளது" },
  te: { Transfers: "బదిలీలు", Scans: "స్కాన్‌లు", Reconciliations: "సమన్వయాలు", Events: "సంఘటనలు", Removed: "తొలగించినవి", Evaluating: "మూల్యాంకనంలో ఉంది", Exception: "మినహాయింపు", Reconciled: "సమన్వయించబడింది", Bundles: "కట్టలు", Alerts: "హెచ్చరికలు", Scripts: "జవాబు పత్రాలు", Dispatches: "పంపిణీలు", Packets: "ప్యాకెట్లు", Open: "తెరిచి ఉంది", Acknowledged: "అంగీకరించబడింది", Registered: "నమోదైంది", Received: "అందుకుంది", Assigned: "కేటాయించబడింది", Stored: "నిల్వ చేయబడింది", Submitted: "సమర్పించబడింది", Archived: "భద్రపరచబడింది", Requested: "అభ్యర్థించబడింది", Authorized: "అధికారమిచ్చారు", "In Transit": "రవాణాలో ఉంది", Rejected: "తిరస్కరించబడింది", Valid: "చెల్లుబాటు", Invalid: "చెల్లదు", Unknown: "తెలియదు", None: "ఏదీ లేదు", Online: "ఆన్‌లైన్", Offline: "ఆఫ్‌లైన్", Queued: "క్యూలో ఉంది", Running: "నడుస్తోంది", Failed: "విఫలమైంది", Returned: "తిరిగి పంపబడింది", Reviewed: "సమీక్షించబడింది", Cleared: "పరిష్కరించబడింది", Approved: "ఆమోదించబడింది", Reopened: "మళ్లీ తెరవబడింది", Resolved: "పరిష్కరించబడింది", Processing: "ప్రాసెసింగ్‌లో", Completed: "పూర్తయింది", Pending: "పెండింగ్‌లో", Active: "సక్రియం", Inactive: "నిష్క్రియం", Ready: "సిద్ధం", Attention: "శ్రద్ధ అవసరం", Cloud: "క్లౌడ్", Core: "ప్రధాన", Healthy: "ఆరోగ్యకరం" },
  mr: { Transfers: "हस्तांतरणे", Scans: "स्कॅन", Reconciliations: "ताळमेळ", Events: "घटना", Removed: "काढलेले", Evaluating: "मूल्यमापन सुरू", Exception: "अपवाद", Reconciled: "ताळमेळ पूर्ण", Bundles: "संच", Alerts: "सूचना", Scripts: "उत्तरपत्रिका", Dispatches: "पाठवण्या", Packets: "पॅकेट्स", Open: "उघडे", Acknowledged: "स्वीकारले", Registered: "नोंदणीकृत", Received: "प्राप्त", Assigned: "वाटप केले", Stored: "साठवले", Submitted: "सादर केले", Archived: "संग्रहित", Requested: "विनंती केलेली", Authorized: "अधिकृत", "In Transit": "वाहतुकीत", Rejected: "नाकारले", Valid: "वैध", Invalid: "अवैध", Unknown: "अज्ञात", None: "काहीही नाही", Online: "ऑनलाइन", Offline: "ऑफलाइन", Queued: "रांगेत", Running: "चालू", Failed: "अयशस्वी", Returned: "परत केले", Reviewed: "पुनरावलोकित", Cleared: "निकाली काढले", Approved: "मंजूर", Reopened: "पुन्हा उघडले", Resolved: "निराकरण झाले", Processing: "प्रक्रिया सुरू", Completed: "पूर्ण", Pending: "प्रलंबित", Active: "सक्रिय", Inactive: "निष्क्रिय", Ready: "तयार", Attention: "लक्ष आवश्यक", Cloud: "क्लाउड", Core: "मुख्य", Healthy: "निरोगी" },
};
type Template = { pattern: RegExp; render: (values: string[]) => string };
const localePhrases = Object.fromEntries(Object.keys(localeResources).map((language) => [language, {}])) as Catalogue;
const localeTemplates = Object.fromEntries(Object.keys(localeResources).map((language) => [language, []])) as unknown as Record<LanguageCode, Template[]>;

function escapePattern(value: string) { return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
function collectLocalePhrases(english: unknown, translated: unknown, language: LanguageCode) {
  if (typeof english === "string" && typeof translated === "string") {
    if (!english.includes("{{")) { localePhrases[language][english] = translated; return; }
    const names: string[] = []; let lastIndex = 0; let pattern = "^"; const token = /\{\{(\w+)\}\}/g; let match: RegExpExecArray | null;
    while ((match = token.exec(english))) { pattern += `${escapePattern(english.slice(lastIndex, match.index))}(.+?)`; names.push(match[1]); lastIndex = match.index + match[0].length; }
    pattern += `${escapePattern(english.slice(lastIndex))}$`;
    localeTemplates[language].push({ pattern: new RegExp(pattern), render: (values) => translated.replace(/\{\{(\w+)\}\}/g, (_, name: string) => values[names.indexOf(name)] ?? "") });
    return;
  }
  if (!english || !translated || typeof english !== "object" || typeof translated !== "object") return;
  for (const key of Object.keys(english as Record<string, unknown>)) collectLocalePhrases((english as Record<string, unknown>)[key], (translated as Record<string, unknown>)[key], language);
}
for (const language of Object.keys(localeResources) as LanguageCode[]) collectLocalePhrases(localeResources.en, localeResources[language], language);

const caseInsensitiveCatalogue = Object.fromEntries((Object.keys(localeResources) as LanguageCode[]).map((language) => [language, new Map([
  ...Object.entries(catalogue[language]),
  ...Object.entries(runtimeTerms[language]),
  ...Object.entries(localePhrases[language]),
  ...Object.entries(recordLocalizations[language]),
  ...Object.entries(supplementalLocalizations[language]),
  ...Object.entries(domainLocalizations[language]),
  ...Object.entries(coverageLocalizations[language]),
].map(([english, translated]) => [english.toLocaleLowerCase("en"), translated]))])) as Record<LanguageCode, Map<string, string>>;
const lookup = (language: LanguageCode, text: string) => coverageLocalizations[language][text] || domainLocalizations[language][text] || supplementalLocalizations[language][text] || recordLocalizations[language][text] || localePhrases[language][text] || runtimeTerms[language][text] || catalogue[language][text] || caseInsensitiveCatalogue[language].get(text.toLocaleLowerCase("en"));
const reverseCatalogue = Object.fromEntries(Object.entries(catalogue).map(([language, entries]) => [language, new Map([...Object.entries(entries), ...Object.entries(runtimeTerms[language as LanguageCode]), ...Object.entries(localePhrases[language as LanguageCode]), ...Object.entries(recordLocalizations[language as LanguageCode]), ...Object.entries(supplementalLocalizations[language as LanguageCode]), ...Object.entries(domainLocalizations[language as LanguageCode]), ...Object.entries(coverageLocalizations[language as LanguageCode])].map(([english, translated]) => [translated, english]))])) as Record<LanguageCode, Map<string, string>>;
const textStates = new WeakMap<Text, { source: string; output: string }>();
const attributeStates = new WeakMap<Element, Map<string, { source: string; output: string }>>();
const originalInputs = new WeakMap<HTMLInputElement, { type: string | null; inputMode: string | null; source: string; output: string }>();
const translatedAttributes = ["aria-label", "alt", "placeholder", "title"];
const digitSets: Partial<Record<LanguageCode, string>> = {
  hi: "०१२३४५६७८९", mr: "०१२३४५६७८९", kn: "೦೧೨೩೪೫೬೭೮೯", ta: "௦௧௨௩௪௫௬௭௮௯", te: "౦౧౨౩౪౫౬౭౮౯",
};
const identifierLetters: Partial<Record<LanguageCode, string[]>> = {
  hi: ["ए", "बी", "सी", "डी", "ई", "एफ", "जी", "एच", "आई", "जे", "के", "एल", "एम", "एन", "ओ", "पी", "क्यू", "आर", "एस", "टी", "यू", "वी", "डब्ल्यू", "एक्स", "वाई", "ज़ेड"],
  kn: ["ಎ", "ಬಿ", "ಸಿ", "ಡಿ", "ಇ", "ಎಫ್", "ಜಿ", "ಎಚ್", "ಐ", "ಜೆ", "ಕೆ", "ಎಲ್", "ಎಂ", "ಎನ್", "ಒ", "ಪಿ", "ಕ್ಯೂ", "ಆರ್", "ಎಸ್", "ಟಿ", "ಯು", "ವಿ", "ಡಬ್ಲ್ಯೂ", "ಎಕ್ಸ್", "ವೈ", "ಝೆಡ್"],
  ta: ["ஏ", "பி", "சி", "டி", "ஈ", "எஃப்", "ஜி", "எச்", "ஐ", "ஜே", "கே", "எல்", "எம்", "என்", "ஓ", "பி", "க்யூ", "ஆர்", "எஸ்", "டி", "யூ", "வி", "டபிள்யூ", "எக்ஸ்", "ஒய்", "ஸெட்"],
  te: ["ఏ", "బి", "సి", "డి", "ఈ", "ఎఫ్", "జి", "హెచ్", "ఐ", "జె", "కె", "ఎల్", "ఎమ్", "ఎన్", "ఓ", "పి", "క్యూ", "ఆర్", "ఎస్", "టి", "యూ", "వి", "డబ్ల్యూ", "ఎక్స్", "వై", "జెడ్"],
  mr: ["ए", "बी", "सी", "डी", "ई", "एफ", "जी", "एच", "आय", "जे", "के", "एल", "एम", "एन", "ओ", "पी", "क्यू", "आर", "एस", "टी", "यू", "व्ही", "डब्ल्यू", "एक्स", "वाय", "झेड"],
};
const locales: Record<LanguageCode, string> = { en: "en-IN", hi: "hi-IN", kn: "kn-IN", ta: "ta-IN", te: "te-IN", mr: "mr-IN" };
const rangeMessages: Record<LanguageCode, string> = {
  en: "Enter a value within the allowed range.",
  hi: "अनुमत सीमा के भीतर कोई मान दर्ज करें।",
  kn: "ಅನುಮತಿಸಲಾದ ವ್ಯಾಪ್ತಿಯೊಳಗೆ ಮೌಲ್ಯವನ್ನು ನಮೂದಿಸಿ.",
  ta: "அனுமதிக்கப்பட்ட வரம்பிற்குள் ஒரு மதிப்பை உள்ளிடவும்.",
  te: "అనుమతించిన పరిధిలో విలువను నమోదు చేయండి.",
  mr: "अनुमत मर्यादेतील मूल्य प्रविष्ट करा.",
};
const allDigitSets = ["0123456789", ...Object.values(digitSets)];

function normalizeDigits(value: string) {
  let result = value;
  for (const digits of allDigitSets) result = result.replace(new RegExp(`[${digits}]`, "g"), (digit) => String(digits.indexOf(digit)));
  return result;
}

function recoverEnglish(value: string, language: LanguageCode) {
  const leading = value.match(/^\s*/)?.[0] || "";
  const trailing = value.match(/\s*$/)?.[0] || "";
  const normalized = normalizeDigits(value.trim());
  const source = reverseCatalogue[language].get(normalized) || normalized;
  return `${leading}${source}${trailing}`;
}

function localizeDigits(value: string, language: LanguageCode) {
  const digits = digitSets[language];
  return digits ? value.replace(/\d/g, (digit) => digits[Number(digit)]) : value;
}

function localizeIdentifier(value: string, language: LanguageCode) {
  const letters = identifierLetters[language];
  const localized = letters ? value.replace(/[A-Z]/g, (letter) => letters[letter.charCodeAt(0) - 65]) : value;
  return localizeDigits(localized, language);
}

function transliterateUnknown(value: string, language: LanguageCode) {
  const letters = identifierLetters[language];
  if (!letters) return value;
  return value.replace(/[A-Za-z]/g, (letter) => letters[letter.toUpperCase().charCodeAt(0) - 65]);
}

function translateWords(value: string, language: LanguageCode) {
  return value.replace(/[A-Za-z][A-Za-z’'&/-]*/g, (word) => {
    const direct = lookup(language, word);
    if (direct) return direct;
    const titled = word[0].toUpperCase() + word.slice(1).toLowerCase();
    const translated = lookup(language, titled);
    if (!translated) return transliterateUnknown(word, language);
    return word[0] === word[0].toLowerCase() ? translated.toLocaleLowerCase(language) : translated;
  });
}

function translateValue(value: string, language: LanguageCode) {
  if (language === "en") return value;
  const leading = value.match(/^\s*/)?.[0] || "";
  const trailing = value.match(/\s*$/)?.[0] || "";
  const text = value.trim();
  if (!text) return value;
  const normalizedText = normalizeDigits(text);
  const stableIdentifier = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedText)
    || /^https?:\/\//i.test(normalizedText)
    || /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(normalizedText);
  if (stableIdentifier) return `${leading}${localizeDigits(normalizedText, language)}${trailing}`;
  if (/^(?:[A-Z]+[0-9]+|[A-Z0-9]+(?:-[A-Z0-9]+)+)$/.test(normalizedText)) return `${leading}${localizeIdentifier(normalizedText, language)}${trailing}`;
  if ((/^\d{4}-\d{2}-\d{2}T/.test(normalizedText) || (/\d[/:,-]/.test(normalizedText) && /\d{1,2}:\d{2}|\b(?:AM|PM)\b/i.test(normalizedText)))) {
    const date = new Date(normalizedText);
    if (!Number.isNaN(date.valueOf())) {
      const formatted = new Intl.DateTimeFormat(locales[language], { dateStyle: "medium", timeStyle: "short", hour12: false }).format(date);
      return `${leading}${localizeDigits(formatted, language)}${trailing}`;
    }
  }
  const direct = lookup(language, text);
  if (direct) return `${leading}${localizeDigits(direct, language)}${trailing}`;
  for (const template of localeTemplates[language]) {
    const match = template.pattern.exec(normalizedText);
    if (match) return `${leading}${localizeDigits(template.render(match.slice(1)), language)}${trailing}`;
  }
  const segments = text.split(/(\s+[·|•]\s+)/);
  const translated = segments.map((segment) => {
    const trimmed = segment.trim();
    if (!trimmed || /^[·|•]$/.test(trimmed)) return segment;
    return lookup(language, trimmed) || translateWords(segment.replaceAll("_", " "), language);
  }).join("");
  return `${leading}${localizeDigits(translated, language)}${trailing}`;
}

function excluded(node: Node) {
  const element = node instanceof Element ? node : node.parentElement;
  return Boolean(element?.closest("script, style, code, pre, .brand-name, .avatar, .language-menu, .language-trigger"));
}

function translateTextNode(node: Text, language: LanguageCode) {
  if (excluded(node)) return;
  const current = node.nodeValue || "";
  let state = textStates.get(node);
  if (!state) state = { source: recoverEnglish(current, language), output: current };
  else if (current !== state.output && current !== state.source) state.source = recoverEnglish(current, language);
  const next = translateValue(state.source, language);
  state.output = next;
  textStates.set(node, state);
  if (current !== next) node.nodeValue = next;
}

function translateElementAttributes(element: Element, language: LanguageCode) {
  if (excluded(element)) return;
  let states = attributeStates.get(element);
  if (!states) { states = new Map(); attributeStates.set(element, states); }
  for (const attribute of translatedAttributes) {
    const current = element.getAttribute(attribute);
    if (current === null) continue;
    let state = states.get(attribute);
    if (!state) state = { source: recoverEnglish(current, language), output: current };
    else if (current !== state.output && current !== state.source) state.source = recoverEnglish(current, language);
    const next = translateValue(state.source, language);
    state.output = next;
    states.set(attribute, state);
    if (current !== next) element.setAttribute(attribute, next);
  }
}

function translateInputValue(input: HTMLInputElement, language: LanguageCode) {
  if (!originalInputs.has(input)) originalInputs.set(input, { type: input.getAttribute("type"), inputMode: input.getAttribute("inputmode"), source: recoverEnglish(input.value, language), output: input.value });
  const original = originalInputs.get(input)!;
  const originalType = original.type || "text";
  const localizable = !["checkbox", "radio", "file", "hidden", "password", "email", "url"].includes(originalType);
  if (language === "en") {
    if (original.type === null) input.removeAttribute("type");
    else input.setAttribute("type", originalType);
    if (original.inputMode === null) input.removeAttribute("inputmode");
    else input.setAttribute("inputmode", original.inputMode);
    original.output = input.value;
    return;
  }
  if (originalType === "number") {
    input.setAttribute("type", "text");
    input.setAttribute("inputmode", "decimal");
  }
  if (localizable && input.value && document.activeElement !== input) {
    if (input.value !== original.output && input.value !== original.source) original.source = recoverEnglish(input.value, language);
    const exact = lookup(language, original.source.trim());
    input.value = exact ? localizeDigits(exact, language) : localizeDigits(normalizeDigits(original.source), language);
    original.output = input.value;
  }
}

function translateElement(element: Element, language: LanguageCode) {
  translateElementAttributes(element, language);
  if (element instanceof HTMLInputElement) translateInputValue(element, language);
}

function translateTree(root: Node, language: LanguageCode) {
  if (root instanceof Text) { translateTextNode(root, language); return; }
  if (root instanceof Element) translateElement(root, language);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    if (node instanceof Text) translateTextNode(node, language);
    else if (node instanceof Element) translateElement(node, language);
    node = walker.nextNode();
  }
}

export function FullPageLocalization() {
  const { i18n } = useTranslation();
  const language: LanguageCode = isLanguageCode(i18n.resolvedLanguage) ? i18n.resolvedLanguage : "en";

  useEffect(() => {
    const root = document.body;
    translateTree(root, language);
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === "characterData") translateTree(mutation.target, language);
        else if (mutation.type === "attributes" && mutation.target instanceof Element) translateElement(mutation.target, language);
        else for (const node of mutation.addedNodes) translateTree(node, language);
      }
    });
    observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: translatedAttributes });
    const onInput = (event: Event) => {
      if (event.target instanceof HTMLInputElement) window.setTimeout(() => translateInputValue(event.target as HTMLInputElement, language), 0);
    };
    const onSubmit = (event: Event) => {
      if (!(event.target instanceof HTMLFormElement)) return;
      let invalid: HTMLInputElement | null = null;
      for (const input of event.target.querySelectorAll("input")) {
        if (!originalInputs.has(input)) continue;
        const original = originalInputs.get(input)!;
        const normalized = recoverEnglish(input.value, language).trim();
        input.value = normalized;
        if ((original.type || "text") === "number" && normalized) {
          const value = Number(normalized); const minimum = input.min === "" ? null : Number(input.min); const maximum = input.max === "" ? null : Number(input.max);
          const invalidNumber = !Number.isFinite(value) || (minimum !== null && value < minimum) || (maximum !== null && value > maximum);
          input.setCustomValidity(invalidNumber ? rangeMessages[language] : "");
          if (invalidNumber && !invalid) invalid = input;
        }
      }
      if (invalid) { event.preventDefault(); invalid.reportValidity(); }
      window.setTimeout(() => { if (event.target instanceof HTMLFormElement && event.target.isConnected) translateTree(event.target, language); }, 0);
    };
    root.addEventListener("input", onInput, true);
    root.addEventListener("submit", onSubmit, true);
    return () => { observer.disconnect(); root.removeEventListener("input", onInput, true); root.removeEventListener("submit", onSubmit, true); };
  }, [language]);

  return <LocalizedBrowserControls />;
}
