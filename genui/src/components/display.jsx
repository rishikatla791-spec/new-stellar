import React, { useEffect, useRef, useState } from "react";
import { animate, motion, useReducedMotion } from "motion/react";
import { cn, formatValue, Icon, TINTS, TONES } from "../lib.js";

export function Heading({ props }) {
  const { text, level = 2, eyebrow, subtitle } = props;
  const size = { 1: "text-2xl sm:text-3xl", 2: "text-xl", 3: "text-base", 4: "text-sm" }[level] || "text-xl";
  const Tag = `h${Math.min(Math.max(Number(level) || 2, 1), 4)}`;
  return (
    <div>
      {eyebrow && <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-primary">{eyebrow}</div>}
      <Tag className={cn("font-semibold leading-tight tracking-tight text-foreground", size)}>{text}</Tag>
      {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
    </div>
  );
}

/* Plain text; **bold** and `code` are honoured, nothing else is markup. */
function inline(text) {
  const out = [];
  String(text ?? "").split(/(\*\*[^*]+\*\*|`[^`]+`)/g).forEach((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) out.push(<strong key={i} className="font-semibold text-foreground">{part.slice(2, -2)}</strong>);
    else if (part.startsWith("`") && part.endsWith("`")) out.push(<code key={i} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{part.slice(1, -1)}</code>);
    else if (part) out.push(part);
  });
  return out;
}

export function Text({ props }) {
  const { text, tone = "default", size = "md", weight, align } = props;
  return (
    <p
      className={cn(
        { xs: "text-xs", sm: "text-[13px]", md: "text-sm", lg: "text-base", xl: "text-lg" }[size] || "text-sm",
        TONES[tone] || TONES.default,
        weight === "medium" && "font-medium",
        weight === "semibold" && "font-semibold",
        align === "center" && "text-center",
        align === "right" && "text-right",
        "leading-relaxed",
      )}
    >
      {inline(text)}
    </p>
  );
}

export function Badge({ props }) {
  const { text, tone = "default", icon, pulse } = props;
  return (
    <span className={cn("inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium", TINTS[tone] || TINTS.default)}>
      {pulse && <span className="relative inline-flex size-1.5 rounded-full bg-current"><span className="gu-ping absolute inset-0 rounded-full bg-current" /></span>}
      {icon && <Icon name={icon} size={12} />}
      {text}
    </span>
  );
}

export function IconView({ props }) {
  const { name, size = 18, tone = "muted", chip } = props;
  if (chip) {
    return (
      <span className={cn("grid size-9 place-items-center rounded-lg border", TINTS[tone] || TINTS.muted)}>
        <Icon name={name} size={size} />
      </span>
    );
  }
  return <Icon name={name} size={size} className={TONES[tone] || TONES.muted} />;
}

/* A number that moves to its new value instead of jumping, and lights up
   briefly when it changes. */
/* countIn: count up from zero when first shown (headline figures). A
   table cell starts at its value and only moves when the value changes,
   or every tab switch would set a whole column spinning. */
export function AnimatedNumber({ value, format, currency, decimals, className, countIn = true }) {
  const reduce = useReducedMotion();
  const n = Number(value);
  const numeric = value !== null && value !== "" && !Number.isNaN(n);
  const start = numeric ? (reduce || !countIn ? n : 0) : value;
  const [shown, setShown] = useState(start);
  const from = useRef(numeric ? start : null);
  useEffect(() => {
    if (!numeric) { setShown(value); return; }
    if (reduce) { setShown(n); from.current = n; return; }
    const controls = animate(from.current ?? 0, n, {
      duration: 0.9,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (v) => setShown(v),
    });
    from.current = n;
    return () => controls.stop();
  }, [n, numeric, reduce, value]);
  const text = numeric ? formatValue(shown, format || "number", { currency, decimals: decimals ?? (format === "currency" || format === "compact" ? undefined : Number.isInteger(n) ? 0 : undefined) }) : String(value ?? "");
  return <span className={cn("gu-num", className)}>{text}</span>;
}

function Sparkline({ data, tone }) {
  const pts = (Array.isArray(data) ? data : []).map(Number).filter((v) => !Number.isNaN(v));
  if (pts.length < 2) return null;
  const w = 120, h = 36, min = Math.min(...pts), max = Math.max(...pts), span = max - min || 1;
  const xy = pts.map((v, i) => [(i / (pts.length - 1)) * w, h - 3 - ((v - min) / span) * (h - 6)]);
  const d = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const color = tone === "danger" ? "var(--danger)" : tone === "success" ? "var(--success)" : "var(--primary)";
  const gid = `g${Math.round(Math.random() * 1e9)}`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-9 w-24 shrink-0 overflow-visible" aria-hidden>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <motion.path d={`${d} L${w},${h} L0,${h} Z`} fill={`url(#${gid})`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.8, delay: 0.3 }} />
      <motion.path d={d} fill="none" stroke={color} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1, ease: [0.22, 1, 0.36, 1] }} />
    </svg>
  );
}

/* A headline figure: label, value, change, and an optional trend line. */
export function KPI({ props }) {
  const { label, value, format, currency, decimals, delta, deltaLabel, icon, trend, loading, tone } = props;
  const d = delta === undefined || delta === null || delta === "" ? null : Number(delta);
  const up = d !== null && d >= 0;
  const flashKey = useRef(0);
  const last = useRef(value);
  if (last.current !== value) { last.current = value; flashKey.current += 1; }
  return (
    <div className="gu-surface relative flex h-full flex-col gap-2 overflow-hidden rounded-xl p-4 sm:p-5">
      <motion.span
        key={flashKey.current}
        className="pointer-events-none absolute inset-0 rounded-xl"
        initial={flashKey.current ? { boxShadow: "inset 0 0 0 1px var(--ring)", backgroundColor: "color-mix(in oklch, var(--primary) 9%, transparent)" } : false}
        animate={{ boxShadow: "inset 0 0 0 1px transparent", backgroundColor: "rgba(0,0,0,0)" }}
        transition={{ duration: 1.1, ease: [0.22, 1, 0.36, 1] }}
      />
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        {icon && <span className="grid size-7 place-items-center rounded-lg bg-muted text-muted-foreground"><Icon name={icon} size={14} /></span>}
      </div>
      {loading ? (
        <div className="gu-shimmer h-8 w-32 rounded-md" />
      ) : (
        <div className={cn("text-2xl font-semibold leading-none tracking-tight sm:text-[28px]", TONES[tone] || "text-foreground")}>
          <AnimatedNumber value={value} format={format} currency={currency} decimals={decimals} />
        </div>
      )}
      <div className="mt-auto flex items-end justify-between gap-2">
        {d !== null ? (
          <span className={cn("inline-flex min-w-0 items-center gap-1 whitespace-nowrap text-xs font-medium", up ? "text-success" : "text-danger")}>
            <Icon name={up ? "TrendingUp" : "TrendingDown"} size={13} className="shrink-0" />
            {formatValue(Math.abs(d), "percent")}
            {deltaLabel && <span className="truncate font-normal text-muted-foreground">{deltaLabel}</span>}
          </span>
        ) : <span />}
        {trend && <Sparkline data={trend} tone={d !== null && !up ? "danger" : undefined} />}
      </div>
    </div>
  );
}

/* value: 0-100 (or 0-1). */
export function Progress({ props }) {
  const { label, value = 0, tone = "primary", showValue = true, caption } = props;
  // A fraction (0.42) is read as 42%; anything else as a percentage.
  const raw = Number(value);
  const v = raw > 0 && raw < 1 ? raw * 100 : raw;
  const pct = Math.max(0, Math.min(100, Number.isNaN(v) ? 0 : v));
  const bar = { primary: "bg-primary", success: "bg-success", warning: "bg-warning", danger: "bg-danger", info: "bg-info" }[tone] || "bg-primary";
  return (
    <div className="flex flex-col gap-1.5">
      {(label || showValue) && (
        <div className="flex items-center justify-between text-xs">
          <span className="font-medium text-foreground">{label}</span>
          {showValue && <span className="gu-num text-muted-foreground">{Math.round(pct)}%</span>}
        </div>
      )}
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <motion.div className={cn("h-full rounded-full", bar)} initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }} />
      </div>
      {caption && <span className="text-xs text-muted-foreground">{caption}</span>}
    </div>
  );
}

export function Avatar({ props }) {
  const { name = "", size = 32 } = props;
  const initials = String(name).split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
  const hue = [...String(name)].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  return (
    <span
      className="inline-grid shrink-0 place-items-center rounded-full text-xs font-semibold text-white"
      style={{ width: size, height: size, background: `linear-gradient(135deg, oklch(0.62 0.15 ${hue}), oklch(0.5 0.16 ${(hue + 40) % 360}))` }}
      title={name}
    >
      {initials || "?"}
    </span>
  );
}

/* Rows with an icon or avatar, a title, a description and a trailing
   value or badge. onSelect fires with the item. */
export function List({ props, rt, scope }) {
  const { items = [], onSelect, dividers = true } = props;
  const rows = Array.isArray(items) ? items : [];
  return (
    <ul className={cn("flex flex-col", dividers && "divide-y divide-border")}>
      {rows.map((it, i) => {
        const row = typeof it === "object" && it ? it : { title: String(it) };
        const clickable = !!onSelect;
        const Tag = clickable ? "button" : "div";
        return (
          <motion.li key={row.id ?? i} layout="position" initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(i, 12) * 0.03 }}>
            <Tag
              type={clickable ? "button" : undefined}
              onClick={clickable ? () => rt.dispatch(onSelect, { item: row, index: i }, row) : undefined}
              className={cn("flex w-full items-center gap-3 py-2.5 text-left", clickable && "-mx-2 rounded-lg px-2 transition-colors hover:bg-muted/60")}
            >
              {row.avatar ? <Avatar props={{ name: row.avatar }} /> : row.icon ? (
                <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground"><Icon name={row.icon} size={15} /></span>
              ) : null}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-foreground">{row.title}</span>
                {row.description && <span className="block truncate text-xs text-muted-foreground">{row.description}</span>}
              </span>
              {row.badge && <Badge props={{ text: row.badge, tone: row.badgeTone || "default" }} />}
              {row.value !== undefined && <span className="gu-num shrink-0 text-sm font-medium text-foreground">{formatValue(row.value, row.format, { currency: row.currency })}</span>}
            </Tag>
          </motion.li>
        );
      })}
    </ul>
  );
}

/* columns: [{key, label, align, format, currency}]. Numbers align right
   and use tabular figures. sortable lets the user sort by a column. */
export function Table({ props, rt }) {
  const { columns = [], rows = [], onSelect, sortable = true, compact } = props;
  const [sort, setSort] = useState(null);
  const data = Array.isArray(rows) ? rows.slice() : [];
  if (sort) {
    data.sort((a, b) => {
      const x = a?.[sort.key], y = b?.[sort.key];
      const r = typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""));
      return sort.dir === "asc" ? r : -r;
    });
  }
  const cols = (Array.isArray(columns) ? columns : []).map((c) => (typeof c === "string" ? { key: c, label: c } : c));
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {cols.map((c) => {
              const right = c.align === "right" || ["number", "currency", "percent", "compact"].includes(c.format);
              return (
                <th key={c.key} className={cn("border-b border-border px-3 py-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground", right ? "text-right" : "text-left")}>
                  {sortable ? (
                    <button type="button" className="inline-flex items-center gap-1 hover:text-foreground" onClick={() => setSort((s) => ({ key: c.key, dir: s && s.key === c.key && s.dir === "desc" ? "asc" : "desc" }))}>
                      {c.label ?? c.key}
                      {sort && sort.key === c.key && <Icon name={sort.dir === "asc" ? "ChevronUp" : "ChevronDown"} size={12} />}
                    </button>
                  ) : (c.label ?? c.key)}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {data.map((r, i) => (
            <motion.tr
              key={r?.id ?? i}
              layout="position"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: Math.min(i, 15) * 0.025 }}
              onClick={onSelect ? () => rt.dispatch(onSelect, { item: r, index: i }, r) : undefined}
              className={cn("border-b border-border/60 transition-colors last:border-0 hover:bg-muted/40", onSelect && "cursor-pointer")}
            >
              {cols.map((c) => {
                const right = c.align === "right" || ["number", "currency", "percent", "compact"].includes(c.format);
                const v = r?.[c.key];
                return (
                  <td key={c.key} className={cn("px-3", compact ? "py-2" : "py-2.5", right && "gu-num text-right", c.key === cols[0].key && "font-medium text-foreground")}>
                    {c.format === "badge" ? <Badge props={{ text: v, tone: (c.tones && c.tones[v]) || "default" }} />
                      : typeof v === "number" && c.format ? <AnimatedNumber value={v} format={c.format} currency={c.currency} decimals={c.decimals} countIn={false} />
                      : formatValue(v, c.format, { currency: c.currency, decimals: c.decimals })}
                  </td>
                );
              })}
            </motion.tr>
          ))}
        </tbody>
      </table>
      {!data.length && <div className="py-8 text-center text-sm text-muted-foreground">{props.empty || "Nothing here yet."}</div>}
    </div>
  );
}

export function EmptyState({ props }) {
  const { icon = "Inbox", title, description } = props;
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border px-6 py-10 text-center">
      <span className="grid size-11 place-items-center rounded-full bg-muted text-muted-foreground"><Icon name={icon} size={20} /></span>
      {title && <div className="text-sm font-semibold text-foreground">{title}</div>}
      {description && <div className="max-w-sm text-xs text-muted-foreground">{description}</div>}
    </div>
  );
}

export function Skeleton({ props }) {
  const lines = Math.max(1, Math.min(Number(props.lines) || 3, 12));
  if (props.height) return <div className="gu-shimmer w-full rounded-lg" style={{ height: Number(props.height) }} />;
  return (
    <div className="flex flex-col gap-2">
      {Array.from({ length: lines }, (_, i) => <div key={i} className="gu-shimmer h-3 rounded" style={{ width: `${i === lines - 1 ? 60 : 100 - i * 6}%` }} />)}
    </div>
  );
}

/* items: [{title, description, status: done | active | todo | error}] */
export function Steps({ props }) {
  const items = Array.isArray(props.items) ? props.items : [];
  return (
    <ol className="flex flex-col">
      {items.map((s, i) => {
        const st = s.status || "todo";
        return (
          <li key={s.id ?? i} className="relative flex gap-3 pb-5 last:pb-0">
            {i < items.length - 1 && (
              <span className="absolute left-[11px] top-7 bottom-1 w-px overflow-hidden bg-border">
                <motion.span className="block w-full bg-success" initial={{ height: 0 }} animate={{ height: st === "done" ? "100%" : 0 }} transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }} />
              </span>
            )}
            <span className={cn(
              "relative z-10 grid size-6 shrink-0 place-items-center rounded-full border text-[11px] font-semibold transition-colors",
              st === "done" && "border-success bg-success text-white",
              st === "active" && "border-primary bg-primary/15 text-primary ring-4 ring-primary/15",
              st === "error" && "border-danger bg-danger text-white",
              st === "todo" && "border-border bg-card text-muted-foreground",
            )}>
              {st === "done" ? <Icon name="Check" size={13} strokeWidth={2.5} /> : st === "error" ? <Icon name="X" size={13} strokeWidth={2.5} /> : i + 1}
            </span>
            <div className="min-w-0 pt-0.5">
              <div className={cn("text-sm font-medium", st === "todo" ? "text-muted-foreground" : "text-foreground")}>{s.title}</div>
              {s.description && <div className="mt-0.5 text-xs text-muted-foreground">{s.description}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function Callout({ props }) {
  const { tone = "info", title, text, icon } = props;
  const icons = { info: "Info", success: "CircleCheck", warning: "AlertTriangle", danger: "CircleAlert", primary: "Sparkles" };
  return (
    <div className={cn("flex gap-3 rounded-xl border px-4 py-3", TINTS[tone] || TINTS.info)}>
      <Icon name={icon || icons[tone] || "Info"} size={16} className="mt-0.5 shrink-0" />
      <div className="min-w-0 text-sm">
        {title && <div className="font-semibold">{title}</div>}
        {text && <div className="text-foreground/80">{inline(text)}</div>}
      </div>
    </div>
  );
}

export function Kbd({ props }) {
  return <kbd className="rounded-md border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">{props.text}</kbd>;
}

/* Only data: and blob: images load in the sandbox. */
export function Image({ props }) {
  const src = String(props.src || "");
  if (!/^(data:image\/|blob:)/.test(src)) return null;
  return <img src={src} alt={props.alt || ""} className={cn("w-full object-cover", props.rounded !== false && "rounded-xl")} style={props.height ? { height: Number(props.height) } : undefined} />;
}

export function Stat({ props }) {
  const { label, value, format, currency } = props;
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-lg font-semibold text-foreground"><AnimatedNumber value={value} format={format} currency={currency} /></span>
    </div>
  );
}
