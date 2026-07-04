import { Outlet, NavLink } from "react-router-dom";

const nav = [
  { to: "/dashboard",  label: "Dashboard" },
  { to: "/lots",       label: "Lots" },
  { to: "/yield-map",  label: "Yield Map" },
  { to: "/genealogy",  label: "Genealogy" },
  { to: "/classifier", label: "Classifier" },
  { to: "/upload",     label: "Upload KLARF" },
  { to: "/products",   label: "Products" },
  { to: "/simulator",  label: "Simulator" },
  { to: "/generate",   label: "Generate" },
  { to: "/analytics",  label: "Analytics" },
];

export default function Layout() {
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-52 shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col">
        <div className="px-5 py-5 border-b border-slate-800">
          <span className="text-emerald-400 font-bold text-lg tracking-tight">OpenYield</span>
          <p className="text-slate-500 text-xs mt-0.5">Inspection Platform</p>
        </div>
        <nav className="flex-1 py-4 space-y-0.5 px-2">
          {nav.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `block px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? "bg-emerald-500/20 text-emerald-400 font-medium"
                    : "text-slate-400 hover:text-slate-100 hover:bg-slate-800"
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 border-t border-slate-800 text-slate-600 text-xs">
          v1.0.0 · MIT
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto bg-slate-950 p-6">
        <Outlet />
      </main>
    </div>
  );
}
