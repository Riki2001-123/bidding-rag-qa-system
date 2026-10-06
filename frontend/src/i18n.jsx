import { createContext, useContext, useEffect, useState } from "react";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";

const LocaleContext = createContext(null);
const LANGUAGE_KEY = "riki_showcase_language";

export function LanguageProvider({ children }) {
  const [language, setLanguage] = useState(() => {
    try { return localStorage.getItem(LANGUAGE_KEY) === "en" ? "en" : "zh"; }
    catch { return "zh"; }
  });
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    document.title = language === "zh" ? "Riki · 企业知识问答" : "Riki · Enterprise knowledge answers";
    try { localStorage.setItem(LANGUAGE_KEY, language); } catch { /* Storage may be disabled. */ }
  }, [language]);
  const tr = (zh, en) => language === "zh" ? zh : en;
  const domainLabel = (domain) => ({
    policy: tr("政策", "Policy"), tender: tr("项目", "Tender"),
    enterprise: tr("企业", "Enterprise"), all: tr("全部", "All"),
  })[domain] || domain;
  return <LocaleContext.Provider value={{ language, setLanguage, tr, domainLabel }}>
    <ConfigProvider locale={language === "zh" ? zhCN : enUS}>{children}</ConfigProvider>
  </LocaleContext.Provider>;
}

export function useLocale() { return useContext(LocaleContext); }

export function LanguageSwitch() {
  const { language, setLanguage, tr } = useLocale();
  return <button className="language-switch" onClick={() => setLanguage(language === "zh" ? "en" : "zh")}
    aria-label={tr("切换到英文", "Switch to Chinese")}>中文 <span>/</span> EN <span className="language-dot">{language.toUpperCase()}</span></button>;
}
