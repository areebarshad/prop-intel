import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Nav from "./components/Nav";
import FirmList from "./pages/FirmList";
import FirmDetail from "./pages/FirmDetail";
import Ask from "./pages/Ask";
import Digest from "./pages/Digest";

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
            <Routes>
              <Route path="/" element={<FirmList />} />
              <Route path="/firms/:id" element={<FirmDetail />} />
              <Route path="/ask" element={<Ask />} />
              <Route path="/digest" element={<Digest />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
