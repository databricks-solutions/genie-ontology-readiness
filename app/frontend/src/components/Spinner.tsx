import { Loader2 } from 'lucide-react';

export default function Spinner({
  label,
  size = 20,
}: {
  label?: string;
  size?: number;
}) {
  return (
    <div className="flex items-center gap-2 text-ink-500">
      <Loader2 size={size} className="animate-spin" />
      {label && <span className="text-sm">{label}</span>}
    </div>
  );
}
