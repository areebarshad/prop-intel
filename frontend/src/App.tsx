import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Nav from "./components/Nav";
import ErrorBoundary from "./components/ErrorBoundary";
import FirmList from "./pages/FirmList";
import FirmDetail from "./pages/FirmDetail";
import Ask from "./pages/Ask";
import Alerts from "./pages/Alerts";
import Digest from "./pages/Digest";
import Login from "./pages/Login";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="min-h-screen bg-white text-gray-900">
          <Nav />
          <main>
            <ErrorBoundary>
              <Routes>
                <Route path="/login" element={<Login />} />
                <Route
                  path="/"
                  element={
                    <ErrorBoundary>
                      <FirmList />
                    </ErrorBoundary>
                  }
                />
                <Route
                  path="/firms/:id"
                  element={
                    <ErrorBoundary>
                      <FirmDetail />
                    </ErrorBoundary>
                  }
                />
                <Route
                  path="/ask"
                  element={
                    <ErrorBoundary>
                      <Ask />
                    </ErrorBoundary>
                  }
                />
                <Route
                  path="/alerts"
                  element={
                    <ErrorBoundary>
                      <Alerts />
                    </ErrorBoundary>
                  }
                />
                <Route
                  path="/digest"
                  element={
                    <ErrorBoundary>
                      <Digest />
                    </ErrorBoundary>
                  }
                />
              </Routes>
            </ErrorBoundary>
          </main>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
