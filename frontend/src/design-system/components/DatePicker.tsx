// DatePicker (owner P08): text entry dd/mm/aaaa (keyboard and paste friendly) + native calendar as an
// alternative. Value = ISO "aaaa-mm-dd" or "".
import { forwardRef, useEffect, useState, type InputHTMLAttributes } from "react";
import { parseDmy, toDmy } from "@lib/dates";
import { cn } from "@lib/cn";
import { inputClass } from "./Field";

export interface DatePickerProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> {
  value: string;
  onValueChange: (iso: string) => void;
  onValidity?: (invalid: boolean) => void;
  calendarLabel: string;
  placeholderText: string;
}

export const DatePicker = forwardRef<HTMLInputElement, DatePickerProps>(function DatePicker(
  { value, onValueChange, onValidity, calendarLabel, placeholderText, className, ...rest },
  ref,
) {
  const [text, setText] = useState(toDmy(value));
  useEffect(() => setText(toDmy(value)), [value]);
  return (
    <div className="flex items-stretch gap-2">
      <input
        ref={ref}
        type="text"
        inputMode="numeric"
        placeholder={placeholderText}
        className={cn(inputClass, "tabular-nums", className)}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (e.target.value.trim() === "") {
            onValidity?.(false);
            onValueChange("");
            return;
          }
          const iso = parseDmy(e.target.value);
          onValidity?.(iso === null);
          if (iso) onValueChange(iso);
        }}
        {...rest}
      />
      <input
        type="date"
        aria-label={calendarLabel}
        className={cn(inputClass, "w-12 cursor-pointer px-1")}
        value={value}
        onChange={(e) => {
          onValidity?.(false);
          onValueChange(e.target.value);
        }}
      />
    </div>
  );
});
