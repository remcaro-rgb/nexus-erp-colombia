// MoneyInput / QuantityInput (owner P08). es-CO formatting; the value is a canonical decimal STRING
// ("1234567.89") — never a JS number (T-P08-04). Paste is always allowed (WCAG 3.3.8).
import { forwardRef, useEffect, useState, type InputHTMLAttributes } from "react";
import { DecimalInputError, formatEsCoDecimal, parseEsCoDecimal, PRECISION, type PrecisionKind } from "@lib/money";
import { cn } from "@lib/cn";
import { inputClass } from "./Field";

export interface DecimalInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> {
  /** Canonical decimal string or "" for empty. */
  value: string;
  onValueChange: (canonical: string) => void;
  onValidity?: (message: string | null) => void;
  kind?: PrecisionKind;
  /** Minimum decimals shown when not editing (COP: 0, quantities: 0). */
  displayDecimals?: number;
  prefix?: string;
}

export const DecimalInput = forwardRef<HTMLInputElement, DecimalInputProps>(function DecimalInput(
  { value, onValueChange, onValidity, kind = "amount", displayDecimals = 0, prefix, className, onBlur, onFocus, ...rest },
  ref,
) {
  const scale = PRECISION[kind].scale;
  const show = (v: string) => (v === "" ? "" : formatEsCoDecimal(v, { minFraction: displayDecimals, maxFraction: scale }));
  const [text, setText] = useState(() => show(value));
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing) setText(value === "" ? "" : show(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, editing]);

  function commit(raw: string) {
    if (raw.trim() === "") {
      onValidity?.(null);
      onValueChange("");
      return;
    }
    try {
      const canonical = parseEsCoDecimal(raw, kind);
      onValidity?.(null);
      onValueChange(canonical);
    } catch (e) {
      onValidity?.(e instanceof DecimalInputError ? e.message : String(e));
    }
  }

  return (
    <div className="relative flex items-center">
      {prefix ? (
        <span aria-hidden="true" className="pointer-events-none absolute left-3 text-sm text-muted">
          {prefix}
        </span>
      ) : null}
      <input
        ref={ref}
        type="text"
        inputMode="decimal"
        autoComplete="off"
        className={cn(inputClass, "text-right tabular-nums", prefix ? "pl-7" : "", className)}
        value={text}
        onFocus={(e) => {
          setEditing(true);
          onFocus?.(e);
        }}
        onChange={(e) => {
          setText(e.target.value);
          commit(e.target.value);
        }}
        onBlur={(e) => {
          setEditing(false);
          onBlur?.(e);
        }}
        {...rest}
      />
    </div>
  );
});

export const MoneyInput = forwardRef<HTMLInputElement, Omit<DecimalInputProps, "kind">>(function MoneyInput(props, ref) {
  return <DecimalInput ref={ref} kind="amount" prefix="$" {...props} />;
});

export interface QuantityInputProps extends Omit<DecimalInputProps, "kind"> {
  uom: string;
  uomOptions?: string[];
  onUomChange?: (uom: string) => void;
  uomLabel: string;
}

export const QuantityInput = forwardRef<HTMLInputElement, QuantityInputProps>(function QuantityInput(
  { uom, uomOptions, onUomChange, uomLabel, ...props },
  ref,
) {
  return (
    <div className="flex items-stretch gap-2">
      <div className="flex-1">
        <DecimalInput ref={ref} kind="quantity" {...props} />
      </div>
      {uomOptions && onUomChange ? (
        <select aria-label={uomLabel} className={cn(inputClass, "w-24")} value={uom} onChange={(e) => onUomChange(e.target.value)}>
          {uomOptions.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
      ) : (
        <span className="inline-flex items-center rounded-control border border-line px-2 text-xs text-secondary">
          <span className="nx-sr-only">{uomLabel}: </span>
          {uom}
        </span>
      )}
    </div>
  );
});
