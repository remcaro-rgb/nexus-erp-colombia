// PartyPicker (owner P08): WAI-ARIA combobox searching parties by NIT or name through a caller-supplied search
// (server-side, policy-filtered). NIT input with a DV ("900123456-8") is validated client-side as a convenience.
import { useEffect, useId, useRef, useState } from "react";
import { parseNit, formatNit } from "@lib/nit";
import { cn } from "@lib/cn";
import { useT } from "@i18n/index";
import { inputClass } from "./Field";

export interface PartyOption {
  id: string;
  name: string;
  nit: string;
  dv?: number | null;
}

export interface PartyPickerProps {
  value: PartyOption | null;
  onChange: (p: PartyOption | null) => void;
  search: (q: string, signal: AbortSignal) => Promise<PartyOption[]>;
  label?: string;
  required?: boolean;
}

export function PartyPicker({ value, onChange, search, label, required }: PartyPickerProps) {
  const t = useT();
  const uid = useId().replace(/:/g, "");
  const [q, setQ] = useState(value ? value.name : "");
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<PartyOption[]>([]);
  const [active, setActive] = useState(-1);
  const [dvError, setDvError] = useState(false);
  const ctrl = useRef<AbortController | null>(null);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2 || (value && term === value.name)) {
      setOptions([]);
      return;
    }
    const looksNit = /^[\d.\s]+-\d$/.test(term);
    setDvError(looksNit && !parseNit(term).valid);
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    const h = window.setTimeout(() => {
      search(looksNit ? parseNit(term).body : term, c.signal)
        .then((rs) => {
          if (!c.signal.aborted) {
            setOptions(rs);
            setActive(rs.length ? 0 : -1);
            setOpen(true);
          }
        })
        .catch(() => undefined);
    }, 200);
    return () => {
      window.clearTimeout(h);
      c.abort();
    };
  }, [q, search, value]);

  function choose(p: PartyOption) {
    onChange(p);
    setQ(p.name);
    setOpen(false);
  }

  const listId = `${uid}-list`;
  const errId = `${uid}-err`;
  return (
    <div className="relative flex flex-col gap-1">
      <label htmlFor={`${uid}-in`} className="text-xs font-semibold text-secondary">
        {label ?? t("ds.party.label")}
        {required ? <span aria-hidden="true" className="text-danger"> *</span> : null}
      </label>
      <input
        id={`${uid}-in`}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 ? `${uid}-opt-${active}` : undefined}
        aria-invalid={dvError || undefined}
        aria-describedby={dvError ? errId : undefined}
        aria-required={required || undefined}
        placeholder={t("ds.party.placeholder")}
        className={inputClass}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          if (value) onChange(null);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            setOpen(true);
            setActive((a) => Math.min(options.length - 1, a + 1));
            e.preventDefault();
          } else if (e.key === "ArrowUp") {
            setActive((a) => Math.max(0, a - 1));
            e.preventDefault();
          } else if (e.key === "Enter" && open && active >= 0 && options[active]) {
            choose(options[active] as PartyOption);
            e.preventDefault();
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
      />
      {dvError ? (
        <p id={errId} role="alert" className="text-xs font-medium text-danger">
          {t("ds.party.dvInvalid")}
        </p>
      ) : null}
      <ul
        id={listId}
        role="listbox"
        aria-label={label ?? t("ds.party.label")}
        hidden={!open}
        className="absolute top-full z-[50] mt-1 max-h-64 w-full overflow-y-auto rounded-control border border-line bg-surface shadow-lg"
      >
        {options.length === 0 ? (
          <li role="option" aria-selected={false} aria-disabled="true" className="px-3 py-2 text-sm text-muted">
            {t("ds.party.none")}
          </li>
        ) : (
          options.map((p, i) => (
            <li
              key={p.id}
              id={`${uid}-opt-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(p);
              }}
              className={cn("cursor-pointer px-3 py-2 text-sm", i === active ? "bg-nav-active text-nav-active-fg" : "text-body")}
            >
              <span className="font-medium">{p.name}</span>{" "}
              <span className="text-xs text-muted">{t("company.nit", { nit: formatNit(p.nit, p.dv ?? null) })}</span>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
