import Link from "next/link";
import { ChevronRight } from "lucide-react";
import "@/styles/components/breadcrumbs.css";

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

export function Breadcrumbs({ items }: { items: BreadcrumbItem[] }) {
  return (
    <div className="breadcrumbs">
      {items.map((item, i) => (
        <span className="breadcrumbs__item" key={i}>
          {i > 0 && <ChevronRight size={14} color="var(--color-text-tertiary)" />}
          {item.href ? (
            <Link href={item.href} className="breadcrumbs__link">
              {item.label}
            </Link>
          ) : (
            <span className="breadcrumbs__current">{item.label}</span>
          )}
        </span>
      ))}
    </div>
  );
}
