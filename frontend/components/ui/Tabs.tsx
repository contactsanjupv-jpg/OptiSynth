"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import "@/styles/components/tabs.css";

export interface TabItem {
  label: string;
  href: string;
  /** Match exactly instead of by prefix -- needed for the "Overview" tab so it
   * doesn't stay highlighted while on /projects/1/candidates etc. */
  exact?: boolean;
}

export function Tabs({ items }: { items: TabItem[] }) {
  const pathname = usePathname();
  return (
    <div className="tabs">
      {items.map((item) => {
        const isActive = item.exact ? pathname === item.href : pathname?.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`tabs__item${isActive ? " tabs__item--active" : ""}`}
          >
            {item.label}
          </Link>
        );
      })}
    </div>
  );
}
