import { BrowserRouter, Routes, Route, Navigate, Outlet } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ErrorBoundary from "./components/ErrorBoundary";
import Nav from "./components/Nav";
import { getToken } from "./api/auth";

import LandingPage from "./pages/LandingPage";
import Login from "./pages/Login";
import FirmList from "./pages/FirmList";
import FirmDetail from "./pages/FirmDetail";
import Ask from "./pages/Ask";
import Alerts from "./pages/Alerts";
import Digest from "./pages/Digest";
import Settings from "./pages/Settings";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

function RequireAuth() {
  return getToken() ? <Outlet /> : <Navigate to="/login" replace />;
}

function AppShell() {
  return (
    <>
      <Nav />
      <Outlet />
    </>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<Login />} />
            <Route element={<RequireAuth />}>
              <Route element={<AppShell />}>
                <Route path="/firms" element={<FirmList />} />
                <Route path="/firms/:id" element={<FirmDetail />} />
                <Route path="/ask" element={<Ask />} />
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/digest" element={<Digest />} />
                <Route path="/settings" element={<Settings />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ErrorBoundary>
    </QueryClientProvider>
  );
}
