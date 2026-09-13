import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Shared panel chrome — solid card, no translucency/blur (per owner request). */
export const PANEL_BG = "bg-card"
export const PANEL_BORDER = "border-border/50"
