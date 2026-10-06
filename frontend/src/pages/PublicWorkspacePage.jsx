import { Link } from "react-router-dom";
import { ArrowRightOutlined } from "@ant-design/icons";
import PublicHeader, { PROFILE_URL } from "../components/PublicHeader";
import { useLocale } from "../i18n";

export default function PublicWorkspacePage() {
  const { tr } = useLocale();
  return <div className="login-view"><PublicHeader /><main className="login-layout">
    <div className="login-story"><div className="eyebrow">PUBLIC SHOWCASE</div>
      <h1>{tr("先体验", "Explore the")}<br /><span>{tr("知识问答。", "knowledge demo.")}</span></h1>
      <p>{tr("这个公开站点提供虚构资料与预设回答，便于体验问答、检索和来源核验。", "This public site uses fictional records and preset answers to demonstrate questions, search and source inspection.")}</p>
      <Link className="text-link" to="/demo">{tr("体验免登录演示", "Try the demo without signing in")}<ArrowRightOutlined /></Link>
    </div>
    <section className="signin-card"><span className="eyebrow">LIVE WORKSPACE</span>
      <h2>{tr("真实系统另行接入", "Connect a live workspace separately")}</h2>
      <p>{tr("真实数据与模型服务未在此公开站点开放。如需接入你的业务资料，可联系 Riki 确定开发范围与验收。", "Live data and model services are not available on this public site. Contact Riki to define the scope and acceptance criteria for your business integration.")}</p>
      <a className="text-link" href={PROFILE_URL} target="_blank" rel="noreferrer">{tr("联系 Riki", "Contact Riki")}<ArrowRightOutlined /></a>
      <p><Link to="/">{tr("返回项目介绍", "Return to project overview")}</Link></p>
    </section>
  </main></div>;
}
