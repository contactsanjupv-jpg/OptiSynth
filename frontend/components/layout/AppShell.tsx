import type { ReactNode } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import "@/styles/components/app-shell.css";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-shell__main">{children}</div>
    </div>
  );
}
