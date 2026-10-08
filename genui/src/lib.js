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

/* A name the curated set lacks is mapped by keyword to the nearest icon
   it has ("RupeeSign" -> IndianRupee, "Flight" -> Plane): models guess
   icon names freely, and an empty icon box looks broken. */
const NEAREST = [
  [/rupee|inr/i, "IndianRupee"], [/dollar|usd/i, "DollarSign"], [/euro/i, "Euro"],
  [/money|cash|bank ?note|expense|spend|cost|price|budget/i, "Banknote"], [/wallet|purse/i, "Wallet"],
  [/sav(e|ing)|piggy/i, "PiggyBank"], [/coin/i, "Coins"], [/card|payment/i, "CreditCard"],
  [/chart|graph|stat|analytic|metric/i, "BarChart3"], [/trend|growth|increase|up/i, "TrendingUp"],
  [/count|number|hash|total|sum/i, "Hash"], [/avg|average|calculat|math/i, "Calculator"],
  [/percent/i, "Percent"], [/user|person|member|people|team|customer/i, "Users"],
  [/calendar|date|day|schedule/i, "Calendar"], [/clock|time|hour|duration|deadline/i, "Clock"],
  [/plane|flight|air/i, "Plane"], [/car|taxi|drive|transport|travel|commute/i, "Car"],
  [/hotel|bed|stay|room|sleep/i, "Bed"], [/food|meal|eat|restaurant|dining|utensil/i, "Utensils"],
  [/coffee|cafe|drink/i, "Coffee"], [/shop|cart|buy|store|purchase/i, "ShoppingCart"],
  [/task|todo|check|done|complete/i, "CircleCheck"], [/list/i, "List"],
  [/setting|config|gear|option/i, "Settings"], [/mail|email|message|inbox/i, "Mail"],
  [/home|house/i, "Home"], [/location|place|map|pin/i, "MapPin"], [/star|favou?rite/i, "Star"],
  [/warn|alert|error|danger/i, "AlertTriangle"], [/info|help|question/i, "Info"],
  [/add|plus|new|create/i, "Plus"], [/delete|trash|remove|bin/i, "Trash2"], [/edit|pencil|write/i, "Pencil"],
  [/search|find/i, "Search"], [/filter/i, "Filter"], [/download|export/i, "Download"], [/upload|import/i, "Upload"],
  [/rocket|launch|ship/i, "Rocket"], [/idea|bulb|insight/i, "Lightbulb"], [/target|goal/i, "Target"],
  [/award|trophy|win|prize/i, "Trophy"], [/heart|love|health/i, "Heart"], [/fire|hot|streak/i, "Flame"],
  [/zap|bolt|energy|power|fast/i, "Zap"], [/lock|secur|private/i, "Lock"], [/file|doc|report/i, "FileText"],
  [/folder|project/i, "Folder"], [/code|dev/i, "Code"], [/bug/i, "Bug"], [/server|database|data/i, "Database"],
  [/history|activity|log/i, "History"], [/gift/i, "Gift"], [/ticket|event/i, "Ticket"], [/sparkle|magic|ai/i, "Sparkles"],
];

export function iconFor(name) {
  if (!name || typeof name !== "string") return null;
  if (ICONS[name]) return ICONS[name];
  const hit = NEAREST.find(([re]) => re.test(name));
  return hit ? ICONS[hit[1]] || null : null;
}

/* A Lucide icon by name (or its nearest); nothing if there is none, so
   callers can leave out the box it would sit in. */
export function Icon({ name, className, size = 16, strokeWidth = 1.75, ...rest }) {
  const C = iconFor(name);
  if (!C) return null;
  return createElement(C, { size, strokeWidth, className, "aria-hidden": true, ...rest });
}

export const hasIcon = (name) => !!iconFor(name);

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
