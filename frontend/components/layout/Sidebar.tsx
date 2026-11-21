"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Settings, Atom, LogOut, ClipboardList, LayoutGrid } from "lucide-react";
import { getSessionCache, clearSessionCache } from "@/lib/auth/session";
import { logout } from "@/lib/api/auth";
import { useEffect, useState } from "react";
import "@/styles/components/sidebar.css";

// Customer navigation. The earlier optimization product's pages stay in the code base but are not
// linked and are redirected in next.config.js.
const NAV_ITEMS = [
  { href: "/overview", label: "Overview", icon: LayoutGrid },
  { href: "/programs", label: "Programs", icon: ClipboardList },
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
        {email && (
          <div className="sidebar__user">
            <div className="sidebar__user-avatar">{email.slice(0, 1).toUpperCase()}</div>
            <div className="sidebar__user-info">
              <span className="sidebar__user-name">{email}</span>
            </div>
            <button className="sidebar__logout-btn" onClick={handleLogout} aria-label="Sign out" type="button">
              <LogOut size={15} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
