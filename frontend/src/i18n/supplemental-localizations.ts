import type { LanguageCode } from "@/i18n/client";

type SupplementalLocalizations = Record<LanguageCode, Record<string, string>>;

/** Labels that are generated from API enum values or composed at runtime. */
export const supplementalLocalizations: SupplementalLocalizations = {
  en: {},
  hi: {
    Access: "पहुँच", Infrastructure: "अवसंरचना", Browser: "ब्राउज़र", Win32: "विंडोज़ ३२", Current: "वर्तमान", Device: "उपकरण", Devices: "उपकरण", Showing: "दिखा रहा है", View: "देखें", recent: "हाल के", sessions: "सत्र", of: "में से", day: "दिन", "/day": "/दिन",
    "My sign-ins": "मेरे साइन-इन", "Recent sessions": "हाल के सत्र", "recent sessions": "हाल के सत्र", "View recent": "हाल के देखें", "View all": "सभी देखें", All: "सभी", Policy: "नीति", Warnings: "चेतावनियाँ", Sessions: "सत्र", "In Progress": "प्रगति पर", "In progress": "प्रगति पर", Invited: "आमंत्रित", Draft: "प्रारूप", Drafts: "प्रारूप", Flagged: "चिह्नित", Priority: "प्राथमिकता",
    Page: "पृष्ठ", Pages: "पृष्ठ", P: "पृ.", v: "संस्करण", Photograph: "छायाचित्र", Photocopy: "छायाप्रति", Photocopies: "छायाप्रतियाँ", "Photo copies": "छायाप्रतियाँ", "Last used": "अंतिम उपयोग", "Last seen": "अंतिम बार देखा", Revoke: "रद्द करें", Revoked: "रद्द किया गया",
  },
  kn: {
    Access: "ಪ್ರವೇಶ", Infrastructure: "ಮೂಲಸೌಕರ್ಯ", Browser: "ಬ್ರೌಸರ್", Win32: "ವಿಂಡೋಸ್ ೩೨", Current: "ಪ್ರಸ್ತುತ", Device: "ಸಾಧನ", Devices: "ಸಾಧನಗಳು", Showing: "ತೋರಿಸಲಾಗುತ್ತಿದೆ", View: "ನೋಡಿ", recent: "ಇತ್ತೀಚಿನ", sessions: "ಸೆಷನ್‌ಗಳು", of: "ರಲ್ಲಿ", day: "ದಿನ", "/day": "/ದಿನ",
    "My sign-ins": "ನನ್ನ ಸೈನ್-ಇನ್‌ಗಳು", "Recent sessions": "ಇತ್ತೀಚಿನ ಸೆಷನ್‌ಗಳು", "recent sessions": "ಇತ್ತೀಚಿನ ಸೆಷನ್‌ಗಳು", "View recent": "ಇತ್ತೀಚಿನವುಗಳನ್ನು ನೋಡಿ", "View all": "ಎಲ್ಲವನ್ನೂ ನೋಡಿ", All: "ಎಲ್ಲಾ", Policy: "ನೀತಿ", Warnings: "ಎಚ್ಚರಿಕೆಗಳು", Sessions: "ಸೆಷನ್‌ಗಳು", "In Progress": "ಪ್ರಗತಿಯಲ್ಲಿದೆ", "In progress": "ಪ್ರಗತಿಯಲ್ಲಿದೆ", Invited: "ಆಹ್ವಾನಿಸಲಾಗಿದೆ", Draft: "ಕರಡು", Drafts: "ಕರಡುಗಳು", Flagged: "ಗುರುತಿಸಲಾಗಿದೆ", Priority: "ಆದ್ಯತೆ",
    Page: "ಪುಟ", Pages: "ಪುಟಗಳು", P: "ಪು.", v: "ಆವೃತ್ತಿ", Photograph: "ಛಾಯಾಚಿತ್ರ", Photocopy: "ಛಾಯಾಪ್ರತಿ", Photocopies: "ಛಾಯಾಪ್ರತಿಗಳು", "Photo copies": "ಛಾಯಾಪ್ರತಿಗಳು", "Last used": "ಕೊನೆಯ ಬಳಕೆ", "Last seen": "ಕೊನೆಯದಾಗಿ ಕಂಡದ್ದು", Revoke: "ರದ್ದುಗೊಳಿಸಿ", Revoked: "ರದ್ದುಗೊಳಿಸಲಾಗಿದೆ",
  },
  ta: {
    Access: "அணுகல்", Infrastructure: "உள்கட்டமைப்பு", Browser: "உலாவி", Win32: "விண்டோஸ் ௩௨", Current: "தற்போதைய", Device: "சாதனம்", Devices: "சாதனங்கள்", Showing: "காட்டப்படுகிறது", View: "காண்க", recent: "சமீபத்திய", sessions: "அமர்வுகள்", of: "இல்", day: "நாள்", "/day": "/நாள்",
    "My sign-ins": "எனது உள்நுழைவுகள்", "Recent sessions": "சமீபத்திய அமர்வுகள்", "recent sessions": "சமீபத்திய அமர்வுகள்", "View recent": "சமீபத்தியவற்றைக் காண்க", "View all": "அனைத்தையும் காண்க", All: "அனைத்தும்", Policy: "கொள்கை", Warnings: "எச்சரிக்கைகள்", Sessions: "அமர்வுகள்", "In Progress": "செயலில் உள்ளது", "In progress": "செயலில் உள்ளது", Invited: "அழைக்கப்பட்டது", Draft: "வரைவு", Drafts: "வரைவுகள்", Flagged: "குறிக்கப்பட்டது", Priority: "முன்னுரிமை",
    Page: "பக்கம்", Pages: "பக்கங்கள்", P: "பக்.", v: "பதிப்பு", Photograph: "புகைப்படம்", Photocopy: "நகற்படம்", Photocopies: "நகற்படங்கள்", "Photo copies": "நகற்படங்கள்", "Last used": "கடைசியாகப் பயன்படுத்தியது", "Last seen": "கடைசியாகக் கண்டது", Revoke: "திரும்பப் பெறுக", Revoked: "திரும்பப் பெறப்பட்டது",
  },
  te: {
    Access: "ప్రాప్యత", Infrastructure: "మౌలిక సదుపాయాలు", Browser: "బ్రౌజర్", Win32: "విండోస్ ౩౨", Current: "ప్రస్తుత", Device: "పరికరం", Devices: "పరికరాలు", Showing: "చూపిస్తోంది", View: "చూడండి", recent: "ఇటీవలి", sessions: "సెషన్‌లు", of: "లో", day: "రోజు", "/day": "/రోజు",
    "My sign-ins": "నా సైన్-ఇన్‌లు", "Recent sessions": "ఇటీవలి సెషన్‌లు", "recent sessions": "ఇటీవలి సెషన్‌లు", "View recent": "ఇటీవలి వాటిని చూడండి", "View all": "అన్నీ చూడండి", All: "అన్నీ", Policy: "విధానం", Warnings: "హెచ్చరికలు", Sessions: "సెషన్‌లు", "In Progress": "పురోగతిలో ఉంది", "In progress": "పురోగతిలో ఉంది", Invited: "ఆహ్వానించబడింది", Draft: "ముసాయిదా", Drafts: "ముసాయిదాలు", Flagged: "గుర్తించబడింది", Priority: "ప్రాధాన్యత",
    Page: "పేజీ", Pages: "పేజీలు", P: "పే.", v: "సంచిక", Photograph: "ఛాయాచిత్రం", Photocopy: "ఛాయాప్రతి", Photocopies: "ఛాయాప్రతులు", "Photo copies": "ఛాయాప్రతులు", "Last used": "చివరిగా ఉపయోగించినది", "Last seen": "చివరిగా చూసినది", Revoke: "రద్దు చేయండి", Revoked: "రద్దు చేయబడింది",
  },
  mr: {
    Access: "प्रवेश", Infrastructure: "पायाभूत सुविधा", Browser: "ब्राउझर", Win32: "विंडोज ३२", Current: "सध्याचे", Device: "उपकरण", Devices: "उपकरणे", Showing: "दाखवत आहे", View: "पहा", recent: "अलीकडील", sessions: "सत्रे", of: "पैकी", day: "दिवस", "/day": "/दिवस",
    "My sign-ins": "माझी साइन-इन", "Recent sessions": "अलीकडील सत्रे", "recent sessions": "अलीकडील सत्रे", "View recent": "अलीकडील पहा", "View all": "सर्व पहा", All: "सर्व", Policy: "धोरण", Warnings: "इशारे", Sessions: "सत्रे", "In Progress": "प्रगतीपथावर", "In progress": "प्रगतीपथावर", Invited: "आमंत्रित", Draft: "मसुदा", Drafts: "मसुदे", Flagged: "चिन्हांकित", Priority: "प्राधान्य",
    Page: "पृष्ठ", Pages: "पृष्ठे", P: "पृ.", v: "आवृत्ती", Photograph: "छायाचित्र", Photocopy: "छायाप्रत", Photocopies: "छायाप्रती", "Photo copies": "छायाप्रती", "Last used": "शेवटचा वापर", "Last seen": "शेवटचे पाहिले", Revoke: "रद्द करा", Revoked: "रद्द केले",
  },
};
