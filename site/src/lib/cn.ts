import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Local stand-in for the design system's `cn` (clsx + tailwind-merge). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
