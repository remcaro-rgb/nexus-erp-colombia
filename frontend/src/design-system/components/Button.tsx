// Button / IconButton (owner P08). Target size ≥ 32 px (tokens.layout.icon_button.min_target_px; WCAG 2.5.8 ≥ 24 px).
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "@lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-brand text-white hover:bg-brand-hover disabled:opacity-60 dark:text-slate-950",
  secondary: "bg-surface text-body border border-line hover:bg-nav-hover disabled:opacity-60",
  ghost: "bg-transparent text-secondary hover:bg-nav-hover disabled:opacity-60",
  danger: "bg-rose-700 text-white hover:bg-rose-600 disabled:opacity-60",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: ReactNode;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", icon, loading, className, children, type = "button", disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex min-h-8 items-center justify-center gap-2 rounded-control px-3 py-1.5 text-sm font-medium transition-colors",
        VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {icon ? <span aria-hidden="true" className="inline-flex">{icon}</span> : null}
      {children}
    </button>
  );
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Accessible name (required: icon-only buttons need a label). */
  label: string;
  icon: ReactNode;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, className, type = "button", title, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={title ?? label}
      className={cn(
        "inline-flex min-h-8 min-w-8 items-center justify-center rounded-control p-2 text-secondary transition-colors hover:bg-nav-hover disabled:opacity-60",
        className,
      )}
      {...rest}
    >
      <span aria-hidden="true" className="inline-flex">{icon}</span>
    </button>
  );
});
