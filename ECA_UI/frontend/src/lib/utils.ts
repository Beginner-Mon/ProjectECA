import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Shared panel chrome — single source for mobile bottom-sheet + desktop floating-panel. */
export const PANEL_BG = "bg-card/80 backdrop-blur-2xl"
export const PANEL_BORDER = "border-border/50"
