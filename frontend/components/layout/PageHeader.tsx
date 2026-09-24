import type { ReactNode } from "react";
import "@/styles/components/page-header.css";

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  /** Optional inline content next to the title, e.g. a StatusBadge. */
  titleAdornment?: ReactNode;
}

export function PageHeader({ title, subtitle, actions, titleAdornment }: PageHeaderProps) {
  return (
    <div className="page-header">
      <div>
        <div className="page-header__title-row">
          <h1 className="page-header__title">{title}</h1>
          {titleAdornment}
        </div>
        {subtitle && <p className="page-header__subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </div>
  );
}
