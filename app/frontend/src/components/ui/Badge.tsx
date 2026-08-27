import type { HTMLAttributes } from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../lib/utils';

// Shared pill badge — Genie Workbench's tier-badge shape (rounded-full, soft bg,
// 1px border, colored text), expressed in OUR palette (light: bg-50 / text-700 /
// border-200). Classes are static so Tailwind v4's scanner keeps them.
const badge = cva(
  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap',
  {
    variants: {
      tone: {
        red: 'bg-red-50 text-red-700 border-red-200',
        orange: 'bg-orange-50 text-orange-700 border-orange-200',
        amber: 'bg-amber-50 text-amber-700 border-amber-200',
        lime: 'bg-lime-50 text-lime-700 border-lime-200',
        emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        violet: 'bg-violet-50 text-violet-700 border-violet-200',
        blue: 'bg-blue-50 text-blue-700 border-blue-200',
        gray: 'bg-gray-100 text-ink-500 border-gray-200',
      },
    },
    defaultVariants: { tone: 'gray' },
  }
);

export type BadgeTone = NonNullable<VariantProps<typeof badge>['tone']>;

export function Badge({
  tone,
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>) {
  return <span className={cn(badge({ tone }), className)} {...props} />;
}

/** Maturity level (0 red → 4 emerald) → badge tone. */
export function levelTone(level: number): BadgeTone {
  return (['red', 'orange', 'amber', 'lime', 'emerald'] as const)[
    Math.max(0, Math.min(4, Math.round(level)))
  ];
}
