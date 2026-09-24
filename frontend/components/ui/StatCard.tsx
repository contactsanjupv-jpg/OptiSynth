import "@/styles/components/stat-card.css";

interface StatCardProps {
  label: string;
  value: string;
  caption?: string;
}

export function StatCard({ label, value, caption }: StatCardProps) {
  return (
    <div className="stat-card">
      <span className="stat-card__label">{label}</span>
      <span className="stat-card__value">{value}</span>
      {caption && <span className="stat-card__caption">{caption}</span>}
    </div>
  );
}
