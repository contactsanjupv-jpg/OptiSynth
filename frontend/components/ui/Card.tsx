import type { HTMLAttributes, ReactNode } from "react";
import "@/styles/components/card.css";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  padding?: "none" | "sm" | "md" | "lg";
}

export function Card({ children, padding = "md", className = "", ...rest }: CardProps) {
  return (
    <div className={`card card--padding-${padding} ${className}`} {...rest}>
      {children}
    </div>
  );
}

export function CardHeader({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`card__header ${className}`}>{children}</div>;
}

export function CardTitle({ children }: { children: ReactNode }) {
  return <h3 className="card__title">{children}</h3>;
}
