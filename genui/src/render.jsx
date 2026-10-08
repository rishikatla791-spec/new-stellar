/* Spec -> React. Every node names a component from the registry; nothing
 * else can be rendered, so a spec cannot run code or style the host. Its
 * props may read the state ({"$bind": "/path"}, "{{/path}}" in text, and
 * {"$item": "field"} inside a Repeat), and its on* props are actions the
 * runtime carries out (see dispatch).
 */
import React, { createContext, useContext } from "react";
import { AnimatePresence, motion } from "motion/react";
import { getPath } from "./store.js";

export const RuntimeCtx = createContext(null);
export const ScopeCtx = createContext({ item: undefined, index: undefined });

let REGISTRY = {};
export function setRegistry(r) { REGISTRY = r; }

const TEMPLATE = /\{\{\s*([^}]+?)\s*\}\}/g;

function lookup(expr, state, scope) {
  const e = expr.trim();
  if (e.startsWith("/")) return getPath(state, e);
  if (e === "index") return scope.index;
  if (e === "item") return scope.item;
  if (e.startsWith("item.")) return getPath(scope.item, "/" + e.slice(5).replace(/\./g, "/"));
  return undefined;
}

export function resolve(value, state, scope) {
  if (typeof value === "string") {
    if (!value.includes("{{")) return value;
    const whole = value.match(/^\{\{\s*([^}]+?)\s*\}\}$/);
    if (whole) return lookup(whole[1], state, scope);   // keeps numbers numbers
    return value.replace(TEMPLATE, (_, e) => {
      const v = lookup(e, state, scope);
      return v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    });
  }
  if (Array.isArray(value)) return value.map((v) => resolve(v, state, scope));
  if (value && typeof value === "object") {
    if ("$bind" in value) return getPath(state, value.$bind);
    if ("$item" in value) return value.$item === "." ? scope.item : getPath(scope.item, "/" + String(value.$item).replace(/\./g, "/"));
    if ("$index" in value) return scope.index;
    if ("$not" in value) return !resolve(value.$not, state, scope);
    if ("$eq" in value && Array.isArray(value.$eq)) {
      const [a, b] = value.$eq.map((v) => resolve(v, state, scope));
      return a === b;
    }
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = resolve(v, state, scope);
    return out;
  }
  return value;
}

/* "if": a binding, "/path" (truthy), "!/path" (falsy), or an expression
   object ({"$eq": ["/tab", "sales"]}). */
function visible(cond, state, scope) {
  if (cond === undefined) return true;
  if (typeof cond === "string" && (cond.startsWith("/") || cond.startsWith("!/"))) {
    const neg = cond.startsWith("!");
    const v = getPath(state, neg ? cond.slice(1) : cond);
    return neg ? !v : !!v;
  }
  return !!resolve(cond, state, scope);
}

export function Unknown({ type }) {
  return (
    <div className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
      Unknown component “{type}”
    </div>
  );
}

export function Node({ node }) {
  const rt = useContext(RuntimeCtx);
  const scope = useContext(ScopeCtx);
  if (!node) return null;
  if (!visible(node.if, rt.state, scope)) return null;
  const Comp = REGISTRY[node.type];
  if (!Comp) return <Unknown type={node.type} />;
  const props = {};
  for (const [k, v] of Object.entries(node.props || {})) {
    // Actions resolve when they run (with the item they ran for); a bind
    // is a path to write to, not a value to read.
    props[k] = k === "bind" || /^on[A-Z]/.test(k) ? v : resolve(v, rt.state, scope);
  }
  return <Comp node={node} props={props} rt={rt} scope={scope} />;
}

function keyOf(child, i) {
  return child.id ? `id:${child.id}` : `${child.type}:${i}`;
}

/* A node's children, each entering with a short rise. On first load they
   come in one after another; later - a node the model inserts - it comes
   in alone, and its siblings slide to make room (layout). */
export function Kids({ node, className, itemClassName, nodes }) {
  const rt = useContext(RuntimeCtx);
  const list = nodes || node.children || [];
  return (
    <AnimatePresence initial={true} mode="popLayout">
      {list.map((child, i) => (
        <motion.div
          key={keyOf(child, i)}
          layout="position"
          className={itemClassName ?? "min-w-0"}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.16 } }}
          transition={{ type: "spring", stiffness: 420, damping: 34, mass: 0.8, delay: rt.booting ? Math.min(i, 12) * 0.045 : 0 }}
        >
          <Node node={child} />
        </motion.div>
      ))}
    </AnimatePresence>
  );
}

/* Children without wrappers, for components that lay them out themselves
   (a button's icon, a form's fields). */
export function Plain({ node }) {
  return (node.children || []).map((child, i) => <Node key={keyOf(child, i)} node={child} />);
}
