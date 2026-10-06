import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
const ShowcasePage = lazy(() => import("./pages/ShowcasePage"));
const DemoPage = lazy(() => import("./pages/DemoPage"));
const PublicWorkspacePage = lazy(() => import("./pages/PublicWorkspacePage"));

export default function PagesApp() {
  return <Suspense fallback={<div className="route-loading"><Spin size="large" /></div>}><Routes>
    <Route path="/" element={<ShowcasePage />} />
    <Route path="/demo" element={<DemoPage />} />
    {["/login", "/chat", "/search", "/dashboard"].map((path) => <Route key={path} path={path} element={<PublicWorkspacePage />} />)}
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense>;
}
