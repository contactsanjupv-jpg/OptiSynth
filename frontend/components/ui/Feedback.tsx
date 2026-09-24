import "@/styles/components/feedback.css";
import { AlertTriangle, Loader2 } from "lucide-react";

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="feedback feedback--loading">
      <Loader2 size={16} className="feedback__spin-icon" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorCallout({ message }: { message: string }) {
  return (
    <div className="feedback feedback--error">
      <AlertTriangle size={16} />
      <span>{message}</span>
    </div>
  );
}
