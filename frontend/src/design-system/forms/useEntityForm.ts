// Forms (owner P08): React Hook Form + Zod (schemas generated from the contracts or written by the module), server
// RFC 9457 problems mapped to fields by JSON pointer, autosave of LOCAL drafts for non-posting documents only, and an
// unsaved-changes guard (beforeunload + in-app navigation blocker).
import { useEffect, useRef, useState, type BaseSyntheticEvent } from "react";
import { useForm, type DefaultValues, type FieldValues, type Path, type UseFormReturn } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import type { ZodType } from "zod";
import { useBlocker } from "react-router";
import { ApiError } from "@lib/api/client";
import { prefKey, readPref, writePref } from "@lib/storage";

export interface EntityFormOptions<T extends FieldValues> {
  schema: ZodType<T>;
  defaultValues: DefaultValues<T>;
  /** Draft key for autosave; omit for posting documents (autosave is for non-posting docs only). */
  draftKey?: string | null;
  posting?: boolean;
  userId?: string | null;
  onSubmit: (values: T) => Promise<void>;
}

export interface EntityForm<T extends FieldValues> {
  form: UseFormReturn<T>;
  submit: (e?: BaseSyntheticEvent) => Promise<void>;
  serverError: ApiError | null;
  draftSavedAt: string | null;
  clearDraft: () => void;
}

/** Map problem.errors[].pointer ("/lines/0/amount" or "#/name") to RHF paths ("lines.0.amount"). */
export function pointerToPath(pointer: string): string {
  return pointer.replace(/^#?\//, "").replace(/\//g, ".");
}

export function applyProblem<T extends FieldValues>(form: UseFormReturn<T>, err: ApiError): boolean {
  let any = false;
  for (const e of err.problem?.errors ?? []) {
    if (!e.pointer) continue;
    form.setError(pointerToPath(e.pointer) as Path<T>, { type: "server", message: e.detail ?? e.code ?? "" });
    any = true;
  }
  return any;
}

export function useEntityForm<T extends FieldValues>(opts: EntityFormOptions<T>): EntityForm<T> {
  const autosave = Boolean(opts.draftKey) && !opts.posting;
  const storageKey = autosave ? prefKey(opts.userId, `draft.${opts.draftKey}`) : null;
  const restored = storageKey ? readPref<{ values: T; at: string } | null>(storageKey, null) : null;
  const form = useForm<T>({
    resolver: zodResolver(opts.schema as never) as never,
    defaultValues: (restored?.values ?? opts.defaultValues) as DefaultValues<T>,
    mode: "onBlur",
  });
  const [serverError, setServerError] = useState<ApiError | null>(null);
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(restored?.at ?? null);
  const submitted = useRef(false);

  useEffect(() => {
    if (!storageKey) return;
    const sub = form.watch((values) => {
      const at = new Date().toISOString();
      writePref(storageKey, { values, at });
      setDraftSavedAt(at);
    });
    return () => sub.unsubscribe();
  }, [form, storageKey]);

  const dirty = form.formState.isDirty && !submitted.current;
  useEffect(() => {
    if (!dirty) return;
    const on = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", on);
    return () => window.removeEventListener("beforeunload", on);
  }, [dirty]);

  const clearDraft = () => {
    if (storageKey) {
      try {
        window.localStorage.removeItem(storageKey);
      } catch {
        /* ignore */
      }
    }
    setDraftSavedAt(null);
  };

  const submit = form.handleSubmit(async (values) => {
    setServerError(null);
    try {
      await opts.onSubmit(values);
      submitted.current = true;
      clearDraft();
      form.reset(values);
    } catch (e) {
      if (e instanceof ApiError) {
        applyProblem(form, e);
        setServerError(e);
      } else throw e;
    }
  });

  return { form, submit, serverError, draftSavedAt, clearDraft };
}

/** In-app navigation guard: returns the blocker so the page can render a confirm dialog (ds.unsaved). */
export function useUnsavedChangesBlocker(dirty: boolean) {
  return useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
}
