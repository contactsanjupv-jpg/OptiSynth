import { EmptyState } from "@/components/ui/EmptyState";

/** An honest state for a tab whose content a later part of the product supplies. It shows no data. */
export function LaterLevelPanel({ title, description, action }: { title: string; description: string; action?: React.ReactNode }) {
  return <EmptyState title={title} description={description} action={action} />;
}
