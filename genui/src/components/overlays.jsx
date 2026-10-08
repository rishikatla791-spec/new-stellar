import React, { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Accordion as RAccordion, Dialog as RDialog, Tabs as RTabs, Tooltip as RTooltip } from "radix-ui";
import { cn, Icon } from "../lib.js";
import { Kids, Node } from "../render.jsx";

/* Tabs: children are Tab nodes ({type: "Tab", id, label, icon}). The
   chosen tab is kept in the state at "bind" (or a private path), so it
   survives a reload and the model can switch it. */
export function Tabs({ node, props, rt, scope }) {
  const tabs = (node.children || []).filter((c) => c.type === "Tab");
  const path = props.bind || `/__ui/tabs/${node.id || "tabs"}`;
  const current = rt.read(path) ?? (tabs[0] && (tabs[0].id || "0"));
  const ids = tabs.map((t, i) => t.id || String(i));
  return (
    <RTabs.Root value={String(current)} onValueChange={(v) => { rt.write(path, v); if (props.onChange) rt.dispatch(props.onChange, scope, v); }} className="flex flex-col gap-4">
      <RTabs.List className={cn("relative flex gap-1 overflow-x-auto", props.variant === "pills" ? "w-fit rounded-lg bg-muted p-1" : "border-b border-border")}>
        {tabs.map((t, i) => {
          const id = ids[i];
          const on = String(current) === id;
          return (
            <RTabs.Trigger key={id} value={id} className={cn("relative flex shrink-0 items-center gap-1.5 px-3 py-2 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring", on ? "text-foreground" : "text-muted-foreground hover:text-foreground", props.variant === "pills" && "rounded-md py-1.5")}>
              {on && (props.variant === "pills"
                ? <motion.span layoutId={`tab-${node.id || "t"}`} className="absolute inset-0 rounded-md bg-card shadow-sm" transition={{ type: "spring", stiffness: 500, damping: 38 }} />
                : <motion.span layoutId={`tab-${node.id || "t"}`} className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" transition={{ type: "spring", stiffness: 500, damping: 38 }} />)}
              <span className="relative z-10 flex items-center gap-1.5">
                {t.props.icon && <Icon name={t.props.icon} size={14} />}
                {t.props.label || id}
                {t.props.count != null && <span className="rounded-full bg-muted px-1.5 text-[10px] text-muted-foreground">{t.props.count}</span>}
              </span>
            </RTabs.Trigger>
          );
        })}
      </RTabs.List>
      <AnimatePresence mode="wait" initial={false}>
        {tabs.map((t, i) => String(current) === ids[i] && (
          <motion.div key={ids[i]} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}>
            <RTabs.Content value={ids[i]} forceMount className="flex flex-col gap-4 outline-none"><Kids node={t} /></RTabs.Content>
          </motion.div>
        ))}
      </AnimatePresence>
    </RTabs.Root>
  );
}

/* Rendered by Tabs; on its own it is just its children. */
export function Tab({ node }) {
  return <div className="flex flex-col gap-4"><Kids node={node} /></div>;
}

export function Accordion({ node, props, rt }) {
  const items = (node.children || []).filter((c) => c.type === "AccordionItem");
  const path = `/__ui/accordion/${node.id || "acc"}`;
  const open = rt.read(path) ?? (props.defaultOpen ? [props.defaultOpen] : []);
  return (
    <RAccordion.Root type="multiple" value={Array.isArray(open) ? open : []} onValueChange={(v) => rt.write(path, v)} className="flex flex-col divide-y divide-border rounded-xl border border-border">
      {items.map((it, i) => {
        const id = it.id || String(i);
        const isOpen = Array.isArray(open) && open.includes(id);
        return (
          <RAccordion.Item key={id} value={id}>
            <RAccordion.Header>
              <RAccordion.Trigger className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm font-medium text-foreground outline-none hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
                <span className="flex items-center gap-2">{it.props.icon && <Icon name={it.props.icon} size={15} className="text-muted-foreground" />}{it.props.title}</span>
                <motion.span animate={{ rotate: isOpen ? 180 : 0 }} transition={{ duration: 0.2 }}><Icon name="ChevronDown" size={15} className="text-muted-foreground" /></motion.span>
              </RAccordion.Trigger>
            </RAccordion.Header>
            <AnimatePresence initial={false}>
              {isOpen && (
                <RAccordion.Content forceMount asChild>
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }} className="overflow-hidden">
                    <div className="flex flex-col gap-3 px-4 pb-4 text-sm text-muted-foreground"><Kids node={it} /></div>
                  </motion.div>
                </RAccordion.Content>
              )}
            </AnimatePresence>
          </RAccordion.Item>
        );
      })}
    </RAccordion.Root>
  );
}

export function AccordionItem({ node }) {
  return <div className="flex flex-col gap-3"><Kids node={node} /></div>;
}

/* While a dialog or sheet is open the frame needs room for it: the frame
   is only as tall as its content, and a modal taller than that would be
   cut off. The runtime reads this to size the frame. */
export const overlayRoom = { count: 0, listeners: new Set() };
function useOverlayRoom(open) {
  useEffect(() => {
    if (!open) return undefined;
    overlayRoom.count += 1;
    overlayRoom.listeners.forEach((fn) => fn());
    return () => { overlayRoom.count -= 1; overlayRoom.listeners.forEach((fn) => fn()); };
  }, [open]);
}

function Overlay({ id, rt, side, title, description, node, props, scope }) {
  const path = `/__ui/open/${id}`;
  const open = !!rt.read(path);
  useOverlayRoom(open);
  const close = () => { rt.write(path, false); if (props.onClose) rt.dispatch(props.onClose, scope); };
  const sheet = !!side;
  return (
    <RDialog.Root open={open} onOpenChange={(v) => (v ? rt.write(path, true) : close())}>
      <AnimatePresence>
        {open && (
          <RDialog.Portal forceMount container={document.getElementById("genui-portal")}>
            <RDialog.Overlay asChild forceMount>
              <motion.div className="fixed inset-0 z-40 bg-black/45 backdrop-blur-[2px]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }} />
            </RDialog.Overlay>
            <RDialog.Content asChild forceMount aria-describedby={description ? undefined : undefined}>
              <motion.div
                className={cn(
                  "fixed z-50 flex flex-col gap-4 border border-border bg-popover text-popover-foreground shadow-2xl outline-none",
                  sheet ? cn("top-0 bottom-0 w-[min(420px,92vw)] p-6", side === "left" ? "left-0 rounded-r-2xl" : "right-0 rounded-l-2xl")
                    : "left-1/2 top-1/2 max-h-[85vh] w-[min(520px,calc(100vw-32px))] overflow-y-auto rounded-2xl p-6",
                )}
                initial={sheet ? { x: side === "left" ? "-100%" : "100%" } : { opacity: 0, scale: 0.96, x: "-50%", y: "-48%" }}
                animate={sheet ? { x: 0 } : { opacity: 1, scale: 1, x: "-50%", y: "-50%" }}
                exit={sheet ? { x: side === "left" ? "-100%" : "100%" } : { opacity: 0, scale: 0.97, x: "-50%", y: "-48%" }}
                transition={{ type: "spring", stiffness: 420, damping: 36 }}
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <RDialog.Title className="text-base font-semibold text-foreground">{title}</RDialog.Title>
                    {description && <RDialog.Description className="mt-1 text-sm text-muted-foreground">{description}</RDialog.Description>}
                  </div>
                  <RDialog.Close className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" aria-label="Close">
                    <Icon name="X" size={16} />
                  </RDialog.Close>
                </div>
                <div className="flex flex-col gap-4"><Kids node={node} /></div>
              </motion.div>
            </RDialog.Content>
          </RDialog.Portal>
        )}
      </AnimatePresence>
    </RDialog.Root>
  );
}

/* Opened by an action ({"open": "<id>"}), closed by {"close": "<id>"},
   Escape, the X or a click outside. */
export function Dialog({ node, props, rt, scope }) {
  return <Overlay id={node.id || "dialog"} rt={rt} node={node} props={props} scope={scope} title={props.title} description={props.description} />;
}

export function Sheet({ node, props, rt, scope }) {
  return <Overlay id={node.id || "sheet"} rt={rt} node={node} props={props} scope={scope} side={props.side === "left" ? "left" : "right"} title={props.title} description={props.description} />;
}

export function Tooltip({ node, props }) {
  return (
    <RTooltip.Provider delayDuration={250}>
      <RTooltip.Root>
        <RTooltip.Trigger asChild><span className="inline-flex">{(node.children || []).map((c, i) => <Node key={c.id || i} node={c} />)}</span></RTooltip.Trigger>
        <RTooltip.Portal container={document.getElementById("genui-portal")}>
          <RTooltip.Content sideOffset={6} className="z-50 rounded-md bg-foreground px-2 py-1 text-xs text-background shadow-lg">
            {props.text}
            <RTooltip.Arrow className="fill-foreground" />
          </RTooltip.Content>
        </RTooltip.Portal>
      </RTooltip.Root>
    </RTooltip.Provider>
  );
}

/* Toasts: raised by the {"toast": ...} action. */
const toastSubs = new Set();
let toastSeq = 0;
export function toast(t) {
  const item = typeof t === "object" && t ? { title: t.title || t.text, description: t.description, tone: t.tone } : { title: String(t) };
  item.id = ++toastSeq;
  toastSubs.forEach((fn) => fn(item));
}

export function Toaster() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const fn = (item) => {
      setItems((xs) => [...xs.slice(-2), item]);
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== item.id)), 3200);
    };
    toastSubs.add(fn);
    return () => toastSubs.delete(fn);
  }, []);
  const icon = { success: "CircleCheck", danger: "CircleAlert", warning: "AlertTriangle" };
  return (
    <div className="pointer-events-none fixed bottom-3 right-3 z-[60] flex w-[min(340px,calc(100vw-24px))] flex-col gap-2" aria-live="polite">
      <AnimatePresence>
        {items.map((t) => (
          <motion.div key={t.id} layout initial={{ opacity: 0, y: 16, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, x: 24 }} transition={{ type: "spring", stiffness: 500, damping: 34 }}
            className="pointer-events-auto flex items-start gap-2.5 rounded-xl border border-border bg-popover px-3.5 py-3 text-sm text-popover-foreground shadow-xl">
            <Icon name={icon[t.tone] || "Info"} size={16} className={cn("mt-0.5 shrink-0", t.tone === "success" ? "text-success" : t.tone === "danger" ? "text-danger" : t.tone === "warning" ? "text-warning" : "text-primary")} />
            <div className="min-w-0">
              <div className="font-medium">{t.title}</div>
              {t.description && <div className="text-xs text-muted-foreground">{t.description}</div>}
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
