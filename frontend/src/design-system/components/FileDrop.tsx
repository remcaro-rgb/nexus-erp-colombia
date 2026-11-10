// FileDrop (owner P08). Dragging is optional: a button opens the file chooser (WCAG 2.5.7 dragging alternative).
import { useId, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { cn } from "@lib/cn";
import { useT } from "@i18n/index";

export function FileDrop({ onFiles, accept, multiple = true, label }: { onFiles: (f: File[]) => void; accept?: string; multiple?: boolean; label: string }) {
  const t = useT();
  const id = useId().replace(/:/g, "");
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [count, setCount] = useState(0);
  const take = (list: FileList | null) => {
    const files = list ? Array.from(list) : [];
    setCount(files.length);
    if (files.length) onFiles(files);
  };
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files);
      }}
      className={cn("flex flex-col items-center gap-2 rounded-card border-2 border-dashed p-6 text-center", over ? "border-brand bg-brand-soft" : "border-line")}
    >
      <Upload aria-hidden="true" size={20} className="text-muted" />
      <p className="text-sm text-body">
        <span id={`${id}-lbl`} className="font-semibold">
          {label}
        </span>
        <br />
        {t("ds.file.drop")}{" "}
        <button type="button" className="min-h-6 font-semibold text-brand underline" onClick={() => input.current?.click()}>
          {t("ds.file.choose")}
        </button>
      </p>
      <input ref={input} type="file" className="nx-sr-only" tabIndex={-1} aria-labelledby={`${id}-lbl`} accept={accept} multiple={multiple} onChange={(e) => take(e.target.files)} />
      <p role="status" className="text-xs text-muted">
        {count ? t("ds.file.selected", { count }) : ""}
      </p>
    </div>
  );
}
