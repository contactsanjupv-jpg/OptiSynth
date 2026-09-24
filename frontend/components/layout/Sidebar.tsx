"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard, FolderKanban, Database, FlaskConical, FileBarChart,
  Users, CreditCard, Settings, CircleHelp, Atom, LogOut, ShieldCheck,
} from "lucide-react";
import { getSessionCache, clearSessionCache } from "@/lib/auth/session";
import { logout } from "@/lib/api/auth";
import { useEffect, useState } from "react";
import "@/styles/components/sidebar.css";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/projects", label: "Projects", icon: FolderKanban },
  { href: "/change-cases", label: "Change Cases", icon: ShieldCheck },
  { href: "/datasets", label: "Datasets", icon: Database },
  { href: "/experiments", label: "Experiments", icon: FlaskConical },
  { href: "/reports", label: "Reports", icon: FileBarChart },
  { href: "/team", label: "Team", icon: Users },
  { href: "/billing", label: "Billing", icon: CreditCard },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    setEmail(getSessionCache()?.userEmail ?? null);
  }, []);

  async function handleLogout() {
    try {
      await logout();
    } finally {
      clearSessionCache();
      router.push("/login");
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <Atom size={20} strokeWidth={2.25} className="sidebar__brand-icon" />
        <span className="sidebar__brand-name">OptiSynth</span>
      </div>

      <nav className="sidebar__nav">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href || pathname?.startsWith(item.href + "/");
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`sidebar__link${isActive ? " sidebar__link--active" : ""}`}
            >
              <Icon size={17} strokeWidth={2} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="sidebar__footer">
        <Link href="/help" className="sidebar__link sidebar__link--muted">
          <CircleHelp size={17} strokeWidth={2} />
          <span>Help</span>
        </Link>
        {email && (
          <div className="sidebar__user">
            <div className="sidebar__user-avatar">{email.slice(0, 1).toUpperCase()}</div>
            <div className="sidebar__user-info">
              <span className="sidebar__user-name">{email}</span>
            </div>
            <button className="sidebar__logout-btn" onClick={handleLogout} aria-label="Log out" type="button">
              <LogOut size={15} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
