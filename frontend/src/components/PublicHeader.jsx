import { Link, NavLink } from "react-router-dom";
import { ArrowUpOutlined } from "@ant-design/icons";
import { LanguageSwitch, useLocale } from "../i18n";

export const PROFILE_URL = "https://github.com/Riki2001-123";
export const REPO_URL = `${PROFILE_URL}/bidding-rag-qa-system`;

export function Brand() {
  return <Link to="/" className="brand"><span className="brand-mark">r<span>·</span></span><span>Riki<span className="brand-caption"> / KNOWLEDGE AI</span></span></Link>;
}

export default function PublicHeader() {
  const { tr } = useLocale();
  return <header className="public-header"><div className="public-header-inner">
    <Brand />
    <nav aria-label={tr("主导航", "Main navigation")}>
      <NavLink to="/" end>{tr("项目介绍", "Overview")}</NavLink>
      <NavLink to="/demo">{tr("交互演示", "Try demo")}</NavLink>
      <a href={REPO_URL} target="_blank" rel="noreferrer">GitHub <ArrowUpOutlined className="external-arrow" /></a>
    </nav>
    <LanguageSwitch />
  </div></header>;
}
