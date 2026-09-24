import type { ButtonHTMLAttributes, ReactNode } from "react";
import "@/styles/components/button.css";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  icon?: ReactNode;
  loading?: boolean;
}

export function Button({
  children,
  variant = "secondary",
  size = "md",
  icon,
  loading = false,
  disabled,
  className = "",
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`btn btn--${variant} btn--${size} ${className}`}
      disabled={disabled || loading}
      {...rest}
    >
      {icon && <span className="btn__icon">{icon}</span>}
      {loading ? "Working…" : children}
    </button>
  );
}
