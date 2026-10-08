import React from "react";
import { motion } from "motion/react";
import { Kids, Node, ScopeCtx } from "../render.jsx";
import { cn, Icon, TINTS } from "../lib.js";

const GAPS = { 0: "gap-0", 1: "gap-1", 2: "gap-2", 3: "gap-3", 4: "gap-4", 5: "gap-5", 6: "gap-6", 8: "gap-8" };
const gap = (g, d = 4) => GAPS[g] ?? GAPS[d];

export function Fragment({ node }) {
  return <Kids node={node} itemClassName="contents" />;
}

/* The whole interface: a header (eyebrow, title, subtitle, badge) over
   its content. Most interfaces start here. */
export function Page({ node, props }) {
  const { title, subtitle, eyebrow, icon, badge, badgeTone = "primary" } = props;
  return (
    <div className="flex flex-col gap-5">
      {(title || subtitle) && (
        <header className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            {icon && (
              <div className="mt-0.5 grid size-10 shrink-0 place-items-center rounded-xl border border-primary/25 bg-primary/12 text-primary">
                <Icon name={icon} size={20} />
              </div>
            )}
            <div className="min-w-0">
              {eyebrow && <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-primary">{eyebrow}</div>}
              {title && <h1 className="text-xl font-semibold leading-tight tracking-tight text-foreground sm:text-2xl">{title}</h1>}
              {subtitle && <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>}
            </div>
          </div>
          {badge && (
            <span className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider", TINTS[badgeTone] || TINTS.primary)}>
              {badgeTone === "success" && (
                <span className="relative inline-flex size-1.5 rounded-full bg-current"><span className="gu-ping absolute inset-0 rounded-full bg-current" /></span>
              )}
              {badge}
            </span>
          )}
        </header>
      )}
      <div className="flex flex-col gap-4"><Kids node={node} /></div>
    </div>
  );
}

export function Stack({ node, props }) {
  return <div className={cn("flex flex-col", gap(props.gap, 3))}><Kids node={node} /></div>;
}

export function Row({ node, props }) {
  const { align = "center", justify = "start", wrap = true } = props;
  return (
    <div
      className={cn(
        "flex",
        gap(props.gap, 3),
        wrap && "flex-wrap",
        { start: "items-start", center: "items-center", end: "items-end", stretch: "items-stretch" }[align],
        { start: "justify-start", center: "justify-center", end: "justify-end", between: "justify-between" }[justify],
      )}
    >
      <Kids node={node} itemClassName={props.grow ? "min-w-0 flex-1" : "min-w-0"} />
    </div>
  );
}

/* Columns that fit: "min" is the narrowest a column may get (px), so the
   grid has as many as fit and drops to one on a phone. "cols" caps them. */
export function Grid({ node, props }) {
  const min = Math.max(120, Math.min(Number(props.min) || 180, 600));
  const cols = Number(props.cols) || 0;
  const style = {
    gridTemplateColumns: cols
      ? `repeat(auto-fit, minmax(max(${min}px, calc((100% - ${(cols - 1) * 16}px) / ${cols})), 1fr))`
      : `repeat(auto-fit, minmax(${min}px, 1fr))`,
  };
  return (
    <div className={cn("grid", gap(props.gap, 4))} style={style}>
      <Kids node={node} itemClassName="min-w-0 [&>*]:h-full" />
    </div>
  );
}

/* The surface everything sits on. variant: default | outline | glass |
   accent | ghost. Glass only reads over something - use it sparingly. */
export function Card({ node, props }) {
  const { title, description, icon, variant = "default", padding = "md", hover = false } = props;
  const pad = { none: "p-0", sm: "p-3", md: "p-4 sm:p-5", lg: "p-6 sm:p-7" }[padding] || "p-5";
  return (
    <motion.section
      whileHover={hover ? { y: -2 } : undefined}
      transition={{ type: "spring", stiffness: 400, damping: 30 }}
      className={cn(
        "relative flex h-full flex-col overflow-hidden rounded-xl",
        variant === "default" && "gu-surface",
        variant === "outline" && "border border-border bg-transparent",
        variant === "glass" && "gu-glass",
        variant === "accent" && "gu-accent-card",
        variant === "ghost" && "bg-transparent",
        hover && "transition-shadow hover:shadow-lg",
        pad,
      )}
    >
      {(title || icon) && (
        <div className="mb-4 flex items-start gap-3">
          {icon && (
            <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
              <Icon name={icon} size={16} />
            </div>
          )}
          <div className="min-w-0">
            {title && <h3 className="text-sm font-semibold leading-tight text-card-foreground">{title}</h3>}
            {description && <p className="mt-1 text-xs text-muted-foreground">{description}</p>}
          </div>
        </div>
      )}
      <div className={cn("flex flex-1 flex-col", props.gap != null ? gap(props.gap) : "gap-3")}><Kids node={node} /></div>
    </motion.section>
  );
}

export function Section({ node, props }) {
  return (
    <section className="flex flex-col gap-3">
      {(props.title || props.description) && (
        <div>
          {props.title && <h2 className="text-sm font-semibold text-foreground">{props.title}</h2>}
          {props.description && <p className="mt-0.5 text-xs text-muted-foreground">{props.description}</p>}
        </div>
      )}
      <div className={cn("flex flex-col", gap(props.gap, 3))}><Kids node={node} /></div>
    </section>
  );
}

export function Divider({ props }) {
  if (props.label) {
    return (
      <div className="flex items-center gap-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        <span className="h-px flex-1 bg-border" />{props.label}<span className="h-px flex-1 bg-border" />
      </div>
    );
  }
  return <hr className="my-1 border-0 border-t border-border" />;
}

export function Spacer({ props }) {
  return <div style={{ height: Math.max(0, Math.min(Number(props.size) || 16, 200)) }} />;
}

/* One copy of its children per item of a list in the state. Inside,
   {"$item": "name"} and "{{item.name}}" read the item. */
export function Repeat({ node, props }) {
  const items = Array.isArray(props.items) ? props.items : [];
  const layout = props.layout || "stack";
  const min = Math.max(120, Math.min(Number(props.min) || 200, 600));
  const wrapper =
    layout === "grid" ? { className: cn("grid", gap(props.gap, 4)), style: { gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))` } }
    : layout === "row" ? { className: cn("flex flex-wrap", gap(props.gap, 3)) }
    : { className: cn("flex flex-col", gap(props.gap, 3)) };
  if (!items.length && props.empty) {
    return <div className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">{props.empty}</div>;
  }
  return (
    <div {...wrapper}>
      {items.map((item, index) => (
        <ScopeCtx.Provider key={(item && (item.id ?? item.key)) ?? index} value={{ item, index }}>
          <motion.div
            layout="position"
            className="min-w-0 [&>*]:h-full"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 420, damping: 34, delay: Math.min(index, 12) * 0.03 }}
          >
            {(node.children || []).map((c, i) => <Node key={c.id || i} node={c} />)}
          </motion.div>
        </ScopeCtx.Provider>
      ))}
    </div>
  );
}
