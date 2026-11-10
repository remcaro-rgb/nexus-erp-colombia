// Field + Input + Select (owner P08). Every control has a visible label, hint and error wired with aria-describedby.
import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { cn } from "@lib/cn";

export interface FieldRenderProps {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
}

export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
  id: idProp,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  className?: string;
  id?: string;
  children: (p: FieldRenderProps) => ReactNode;
}) {
  const auto = useId();
  const id = idProp ?? `f${auto.replace(/:/g, "")}`;
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-err` : undefined;
  const describedBy = [hintId, errId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <label htmlFor={id} className="text-xs font-semibold text-secondary">
        {label}
        {required ? <span aria-hidden="true" className="text-danger"> *</span> : null}
      </label>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {hint ? (
        <p id={hintId} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errId} role="alert" className="text-xs font-medium text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export const inputClass =
  "min-h-8 w-full rounded-control border border-line bg-input px-3 py-1.5 text-sm text-body placeholder:text-muted aria-[invalid=true]:border-rose-600";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cn(inputClass, className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, ...rest }, ref) {
  return <select ref={ref} className={cn(inputClass, "pr-8", className)} {...rest} />;
});
