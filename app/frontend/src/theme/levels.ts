// Color mapping for the 0-4 maturity levels (0 red -> 4 green).

export interface LevelStyle {
  bg: string;
  text: string;
  ring: string;
  hex: string;
}

const LEVEL_STYLES: LevelStyle[] = [
  { bg: 'bg-red-100', text: 'text-red-700', ring: 'ring-red-200', hex: '#dc2626' }, // 0 Absent
  { bg: 'bg-orange-100', text: 'text-orange-700', ring: 'ring-orange-200', hex: '#ea580c' }, // 1 Initial
  { bg: 'bg-amber-100', text: 'text-amber-700', ring: 'ring-amber-200', hex: '#d97706' }, // 2 Developing
  { bg: 'bg-lime-100', text: 'text-lime-700', ring: 'ring-lime-200', hex: '#65a30d' }, // 3 Established
  { bg: 'bg-emerald-100', text: 'text-emerald-700', ring: 'ring-emerald-200', hex: '#059669' }, // 4 Optimized
];

export function levelStyle(level: number): LevelStyle {
  const idx = Math.max(0, Math.min(4, Math.round(level)));
  return LEVEL_STYLES[idx];
}

export function scoreColor(score: number): string {
  if (score >= 85) return '#059669';
  if (score >= 65) return '#65a30d';
  if (score >= 40) return '#d97706';
  if (score > 0) return '#ea580c';
  return '#dc2626';
}
