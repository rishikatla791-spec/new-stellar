import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { createElement } from "react";
import { ICONS } from "./icons.js";

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

/* Numbers as people read them. "compact" is 12.4K; currency follows the
   currency's own locale so ₹1,39,350 groups the Indian way. */
export function formatValue(value, format, { currency = "USD", decimals, locale } = {}) {
  if (value === null || value === undefined || value === "") return "";
  const n = typeof value === "number" ? value : Number(value);
  if (!format || Number.isNaN(n)) return String(value);
  const loc = locale || (currency === "INR" ? "en-IN" : undefined);
  const d = decimals ?? undefined;
  try {
    switch (format) {
      case "currency":
        return new Intl.NumberFormat(loc, { style: "currency", currency, maximumFractionDigits: d ?? (Math.abs(n) >= 1000 ? 0 : 2), minimumFractionDigits: d ?? 0 }).format(n);
      case "percent":
        return new Intl.NumberFormat(loc, { style: "percent", maximumFractionDigits: d ?? 1 }).format(Math.abs(n) > 1 ? n / 100 : n);
      case "compact":
        return new Intl.NumberFormat(loc, { notation: "compact", maximumFractionDigits: d ?? 1 }).format(n);
      case "decimal":
        return new Intl.NumberFormat(loc, { minimumFractionDigits: d ?? 2, maximumFractionDigits: d ?? 2 }).format(n);
      default:
        return new Intl.NumberFormat(loc, { maximumFractionDigits: d ?? 2 }).format(n);
    }
  } catch (e) {
    return String(value);
  }
}

/* A Lucide icon by name; an unknown name draws nothing rather than
   breaking the interface. */
export function Icon({ name, className, size = 16, strokeWidth = 1.75, ...rest }) {
  const C = name && ICONS[name];
  if (!C) return null;
  return createElement(C, { size, strokeWidth, className, "aria-hidden": true, ...rest });
}

export const TONES = {
  default: "text-foreground",
  muted: "text-muted-foreground",
  primary: "text-primary",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
};

/* Tinted backgrounds for badges, callouts and icon chips. */
export const TINTS = {
  default: "bg-secondary text-secondary-foreground border-border",
  primary: "bg-primary/12 text-primary border-primary/25",
  success: "bg-success/12 text-success border-success/25",
  warning: "bg-warning/14 text-warning border-warning/30",
  danger: "bg-danger/12 text-danger border-danger/25",
  info: "bg-info/12 text-info border-info/25",
  muted: "bg-muted text-muted-foreground border-border",
};
