"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { setApiLang } from "./api";
import type { Lang } from "./types";

type Dict = Record<string, string>;
const en: Dict = {
  "nav.dashboard": "Dashboard", "nav.batches": "Batches", "nav.tests": "Tests", "nav.investigations": "Investigations", "nav.alerts": "Alerts", "nav.insights": "Insights",
  "nav.projects": "Projects", "nav.risk": "Risk map", "nav.evidence": "Evidence", "nav.advisor": "Test advisor", "nav.assistant": "Assistant", "nav.audit": "Audit log", "nav.admin": "Administration",
  "nav.sync": "Sync", "nav.account": "Account", "nav.more": "More", "nav.quality": "Quality", "nav.site": "Site", "nav.ai": "AI", "nav.manage": "Manage",
  "common.save": "Save", "common.cancel": "Cancel", "common.delete": "Delete", "common.retry": "Try again", "common.search": "Search", "common.clear": "Clear", "common.loadMore": "Load more",
  "common.create": "Create", "common.back": "Back", "common.close": "Close", "common.copy": "Copy", "common.open": "Open", "common.all": "All", "common.optional": "optional", "common.required": "required",
  "common.loading": "Loading", "common.signOut": "Sign out", "common.signIn": "Sign in", "common.language": "Language", "common.project": "Project", "common.status": "Status", "common.details": "Details",
  "status.VERIFIED": "Verified", "status.REVIEW_REQUIRED": "Review required", "status.FLAGGED": "Flagged", "status.PENDING": "Pending",
  "state.verified": "Completed", "state.warning": "Requires attention", "state.failed": "Failed",
  "err.title": "Something went wrong", "err.network": "We couldn't reach the server. Check your connection and try again.", "err.offline": "You are offline. This action needs a connection.",
  "err.timeout": "This is taking longer than expected. Please try again.", "err.auth": "Your session has ended. Please sign in again.", "err.forbidden": "You don't have permission to do this.",
  "err.notfound": "We couldn't find that.", "err.validation": "Please check the highlighted fields.", "err.conflict": "This conflicts with existing data.", "err.rate": "Too many requests. Please wait a moment.",
  "err.server": "The service had a problem. Please try again shortly.", "err.unknown": "Something went wrong. Please try again.", "err.ai": "The AI assistant is unavailable right now.",
  "offline.banner": "You're offline. Showing saved data; changes you make are kept on this device until you reconnect.", "offline.saved": "Saved data from",
  "login.title": "Sign in to BUILDGUARD", "login.email": "Email", "login.password": "Password", "login.register": "Create an account", "login.haveAccount": "Already have an account?",
  "dash.title": "Dashboard", "dash.empty": "No batches yet", "batches.title": "Batches", "batches.new": "Register batch", "batches.search": "Search supplier or batch code",
  "assistant.title": "Assistant", "assistant.placeholder": "Ask about IS codes, a batch, or your project…", "assistant.send": "Send", "assistant.stop": "Stop", "assistant.new": "New chat",
  "assistant.notConnected": "AI is not configured. Add AI_PROVIDER and AI_API_KEY to .env.local and restart the backend. Until then, answers come only from verified IS-code notes and your project documents.",
};
const hi: Dict = {
  "nav.dashboard": "डैशबोर्ड", "nav.batches": "बैच", "nav.tests": "परीक्षण", "nav.investigations": "जाँच", "nav.alerts": "अलर्ट", "nav.insights": "इनसाइट्स", "nav.projects": "प्रोजेक्ट",
  "nav.risk": "जोखिम मानचित्र", "nav.evidence": "साक्ष्य", "nav.advisor": "परीक्षण सलाहकार", "nav.assistant": "सहायक", "nav.audit": "ऑडिट लॉग", "nav.admin": "प्रशासन", "nav.sync": "सिंक",
  "nav.account": "खाता", "nav.more": "और", "nav.quality": "गुणवत्ता", "nav.site": "साइट", "nav.ai": "AI", "nav.manage": "प्रबंधन",
  "common.save": "सहेजें", "common.cancel": "रद्द करें", "common.delete": "हटाएँ", "common.retry": "फिर कोशिश करें", "common.search": "खोजें", "common.clear": "साफ़ करें", "common.loadMore": "और दिखाएँ",
  "common.create": "बनाएँ", "common.back": "वापस", "common.close": "बंद करें", "common.copy": "कॉपी", "common.open": "खोलें", "common.all": "सभी", "common.optional": "वैकल्पिक", "common.required": "आवश्यक",
  "common.loading": "लोड हो रहा है", "common.signOut": "साइन आउट", "common.signIn": "साइन इन", "common.language": "भाषा", "common.project": "प्रोजेक्ट", "common.status": "स्थिति", "common.details": "विवरण",
  "status.VERIFIED": "सत्यापित", "status.REVIEW_REQUIRED": "समीक्षा आवश्यक", "status.FLAGGED": "चिह्नित", "status.PENDING": "लंबित",
  "state.verified": "पूर्ण", "state.warning": "ध्यान देना आवश्यक", "state.failed": "विफल",
  "err.title": "कुछ गड़बड़ हो गई", "err.network": "सर्वर तक नहीं पहुँच सके। अपना कनेक्शन जाँचकर फिर कोशिश करें।", "err.offline": "आप ऑफ़लाइन हैं। इस कार्य के लिए कनेक्शन चाहिए।",
  "err.timeout": "इसमें अपेक्षा से अधिक समय लग रहा है। कृपया फिर कोशिश करें।", "err.auth": "आपका सत्र समाप्त हो गया है। कृपया फिर साइन इन करें।", "err.forbidden": "आपको यह करने की अनुमति नहीं है।",
  "err.notfound": "यह नहीं मिला।", "err.validation": "कृपया चिह्नित फ़ील्ड जाँचें।", "err.conflict": "यह मौजूदा डेटा से मेल नहीं खाता।", "err.rate": "बहुत अधिक अनुरोध। कृपया थोड़ी देर रुकें।",
  "err.server": "सेवा में समस्या आई। कृपया थोड़ी देर बाद कोशिश करें।", "err.unknown": "कुछ गड़बड़ हो गई। कृपया फिर कोशिश करें।", "err.ai": "AI सहायक अभी उपलब्ध नहीं है।",
  "offline.banner": "आप ऑफ़लाइन हैं। सहेजा गया डेटा दिख रहा है; आपके बदलाव कनेक्शन लौटने तक इस डिवाइस पर रहेंगे।", "offline.saved": "सहेजा गया डेटा:",
  "login.title": "BUILDGUARD में साइन इन करें", "login.email": "ईमेल", "login.password": "पासवर्ड", "login.register": "खाता बनाएँ", "login.haveAccount": "पहले से खाता है?",
  "dash.title": "डैशबोर्ड", "dash.empty": "अभी कोई बैच नहीं", "batches.title": "बैच", "batches.new": "बैच दर्ज करें", "batches.search": "सप्लायर या बैच कोड खोजें",
  "assistant.title": "सहायक", "assistant.placeholder": "IS कोड, बैच या अपने प्रोजेक्ट के बारे में पूछें…", "assistant.send": "भेजें", "assistant.stop": "रोकें", "assistant.new": "नई चैट",
  "assistant.notConnected": "AI कॉन्फ़िगर नहीं है। .env.local में AI_PROVIDER और AI_API_KEY जोड़ें और बैकएंड को फिर शुरू करें। तब तक उत्तर केवल सत्यापित IS-कोड नोट्स और आपके प्रोजेक्ट दस्तावेज़ों से आते हैं।",
};
const DICTS: Record<Lang, Dict> = { en, hi };
export const translate = (lang: Lang, key: string) => DICTS[lang][key] ?? en[key] ?? key;

interface Ctx { lang: Lang; setLang: (l: Lang) => void; t: (k: string) => string }
const LangCtx = createContext<Ctx>({ lang: "en", setLang: () => undefined, t: (k) => en[k] ?? k });
export function LangProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("en");
  useEffect(() => { const s = localStorage.getItem("bg.lang"); if (s === "hi" || s === "en") setLangState(s); }, []);
  useEffect(() => { document.documentElement.lang = lang; setApiLang(lang); }, [lang]);
  const setLang = useCallback((l: Lang) => { setLangState(l); localStorage.setItem("bg.lang", l); }, []);       // a language preference is not sensitive
  const value = useMemo(() => ({ lang, setLang, t: (k: string) => translate(lang, k) }), [lang, setLang]);
  return <LangCtx.Provider value={value}>{children}</LangCtx.Provider>;
}
export const useT = () => useContext(LangCtx);
