import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge conditional + conflicting Tailwind classes (Genie Workbench convention). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
