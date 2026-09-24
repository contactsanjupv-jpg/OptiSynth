"use client";

import { Bell } from "lucide-react";
import type { ReactNode } from "react";
import "@/styles/components/topbar.css";

interface TopbarProps {
  /** Breadcrumb or title content, e.g. <Breadcrumbs items={...} /> */
  children?: ReactNode;
}

export function Topbar({ children }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar__breadcrumb">{children}</div>
      <button className="topbar__icon-btn" aria-label="Notifications">
        <Bell size={18} strokeWidth={2} />
      </button>
    </header>
  );
}
