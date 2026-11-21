"use client";

import type { ReactNode } from "react";
import { NotificationBell } from "@/components/settings/NotificationBell";
import "@/styles/components/topbar.css";
import "@/styles/components/settings.css";

interface TopbarProps {
  /** Breadcrumb or title content, e.g. <Breadcrumbs items={...} /> */
  children?: ReactNode;
}

export function Topbar({ children }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar__breadcrumb">{children}</div>
      <NotificationBell />
    </header>
  );
}
