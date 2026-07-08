import { Outlet, NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard, Layers, ScanSearch, Map, GitFork,
  BrainCircuit, Upload, Package, Zap, Sparkles, BarChart3,
  Cpu,
} from "lucide-react";

const nav = [
  { to: "/dashboard",  label: "Dashboard",     icon: LayoutDashboard },
  { to: "/lots",       label: "Lots",           icon: Layers },
  { to: "/defects",    label: "Defect Gallery", icon: ScanSearch },
  { to: "/yield-map",  label: "Yield Map",      icon: Map },
  { to: "/genealogy",  label: "Genealogy",      icon: GitFork },
  { to: "/classifier", label: "Classifier",     icon: BrainCircuit },
  { to: "/upload",     label: "Upload KLARF",   icon: Upload },
  { to: "/products",   label: "Products",       icon: Package },
  { to: "/simulator",  label: "Simulator",      icon: Zap },
  { to: "/generate",   label: "Generate",       icon: Sparkles },
  { to: "/analytics",  label: "Analytics",      icon: BarChart3 },
];

const PAGE_TITLES: Record<string, string> = {
  "/dashboard":  "Dashboard",
  "/lots":       "Lots",
  "/defects":    "Defect Gallery",
  "/yield-map":  "Yield Map",
  "/genealogy":  "Genealogy",
  "/classifier": "Classifier",
  "/upload":     "Upload KLARF",
  "/products":   "Products",
  "/simulator":  "Simulator",
  "/generate":   "Generate",
  "/analytics":  "Analytics",
};

export default function Layout() {
  const { pathname } = useLocation();
  const pageTitle = PAGE_TITLES[pathname] ?? "OpenYield";

  return (
    <div className="flex h-screen overflow-hidden bg-slate-950">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 flex flex-col border-r border-slate-800/60" style={{ background: "rgb(10 13 20)" }}>

        {/* Logo */}
        <div className="px-5 pt-6 pb-5 border-b border-slate-800/60">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center shrink-0">
              <Cpu size={14} className="text-emerald-400" />
            </div>
            <div>
              <span className="text-slate-100 font-bold text-sm tracking-tight">OpenYield</span>
              <p className="text-slate-600 text-[10px] leading-tight mt-0.5">Inspection Platform</p>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-3 px-2 space-y-0.5 overflow-y-auto">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium transition-all ${
                  isActive
                    ? "bg-emerald-500/12 text-emerald-400 border border-emerald-500/20"
                    : "text-slate-500 hover:text-slate-200 hover:bg-slate-800/50 border border-transparent"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon size={14} className={isActive ? "text-emerald-400" : "text-slate-600"} />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-4 py-4 border-t border-slate-800/60 space-y-2">
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[10px] text-slate-500">CHIPS Act · Apache 2.0</span>
          </div>
          <div className="text-[10px] text-slate-700">v1.0.0 · ywoo940912</div>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* Top bar */}
        <header className="h-12 shrink-0 border-b border-slate-800/60 flex items-center px-6 gap-3" style={{ background: "rgb(10 13 20)" }}>
          <span className="text-slate-600 text-xs">/</span>
          <span className="text-slate-300 text-xs font-medium">{pageTitle}</span>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto bg-slate-950 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
