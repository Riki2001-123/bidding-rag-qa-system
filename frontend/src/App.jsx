import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import { getToken } from "./api/client";
const ShowcasePage = lazy(() => import("./pages/ShowcasePage"));
const DemoPage = lazy(() => import("./pages/DemoPage"));
const LayoutShell = lazy(() => import("./layouts/LayoutShell"));
const LoginPage = lazy(() => import("./pages/LoginPage"));
const ChatPage = lazy(() => import("./pages/ChatPage"));
const SearchPage = lazy(() => import("./pages/SearchPage"));
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
function RequireAuth({ children }) { return getToken() ? children : <Navigate to="/login" replace />; }
export default function App() {
  return <Suspense fallback={<div className="route-loading"><Spin size="large" /></div>}><Routes>
    <Route path="/" element={<ShowcasePage />} /><Route path="/demo" element={<DemoPage />} />
    <Route path="/login" element={<LoginPage />} />
    <Route element={<RequireAuth><LayoutShell /></RequireAuth>}>
      <Route path="/chat" element={<ChatPage />} /><Route path="/search" element={<SearchPage />} /><Route path="/dashboard" element={<DashboardPage />} />
    </Route><Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense>;
}
