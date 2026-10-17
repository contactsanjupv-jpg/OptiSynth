"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import "@/styles/components/dialog.css";

interface DialogProps {
  title: string;
  /** Called on Escape or a click on the backdrop. Ignored while `busy`, so an operation in progress
   *  can't be dismissed (and then submitted a second time). */
  onClose: () => void;
  busy?: boolean;
  width?: number;
  children: ReactNode;
  /** The buttons, rendered right-aligned under the content. */
  actions: ReactNode;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Accessible modal dialog: role="dialog" + aria-modal, labelled by its title, focus moves into it
 * when it opens and returns to the control that opened it when it closes, Tab stays inside it, and
 * Escape closes it (unless an operation is in progress).
 */
export function Dialog({ title, onClose, busy = false, width = 480, children, actions }: DialogProps) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const busyRef = useRef(busy);
  onCloseRef.current = onClose;
  busyRef.current = busy;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>("input, select, textarea") ?? panel?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !busyRef.current) {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab" || !panel) return;
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      if (!firstItem || !lastItem) return;
      if (e.shiftKey && document.activeElement === firstItem) {
        e.preventDefault();
        lastItem.focus();
      } else if (!e.shiftKey && document.activeElement === lastItem) {
        e.preventDefault();
        firstItem.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      opener?.focus?.();
    };
  }, []);

  return (
    <div
      className="dialog-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div ref={panelRef} className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} style={{ maxWidth: width }}>
        <h2 id={titleId} className="dialog__title">{title}</h2>
        <div className="dialog__body">{children}</div>
        <div className="dialog__actions">{actions}</div>
      </div>
    </div>
  );
}