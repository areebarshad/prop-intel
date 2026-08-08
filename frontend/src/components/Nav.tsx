import { Link, useLocation } from "react-router-dom";

const links = [
  { to: "/", label: "Firms" },
  { to: "/ask", label: "Ask" },
  { to: "/digest", label: "Digest" },
];

export default function Nav() {
  const { pathname } = useLocation();
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
      </div>
    </nav>
  );
}
