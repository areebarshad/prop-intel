import { Link, useLocation, useNavigate } from "react-router-dom";
import { getToken, logout } from "../api/auth";

const links = [
  { to: "/", label: "Firms" },
  { to: "/ask", label: "Ask" },
  { to: "/alerts", label: "Alerts" },
  { to: "/digest", label: "Digest" },
];

export default function Nav() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const isLoggedIn = !!getToken();

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <nav className="border-b border-gray-200 bg-white sticky top-0 z-10">
      <div className="max-w-5xl mx-auto px-4 h-12 flex items-center gap-6">
        <Link to="/" className="font-semibold text-blue-700 text-sm tracking-wide">
          PropIntel
        </Link>
        {links.map(({ to, label }) => (
          <Link
            key={to}
            to={to}
            className={`text-sm ${
              pathname === to
                ? "text-blue-700 font-medium"
                : "text-gray-500 hover:text-gray-800"
            }`}
          >
            {label}
          </Link>
        ))}
        <div className="ml-auto">
          {isLoggedIn ? (
            <button
              onClick={handleLogout}
              className="text-sm text-gray-500 hover:text-gray-800"
            >
              Sign out
            </button>
          ) : (
            <Link
              to="/login"
              className={`text-sm ${
                pathname === "/login"
                  ? "text-blue-700 font-medium"
                  : "text-gray-500 hover:text-gray-800"
              }`}
            >
              Sign in
            </Link>
          )}
        </div>
      </div>
    </nav>
  );
}
