/* The interface's two halves: the spec (which components, arranged how)
 * and the state (the data they show and the choices made in them).
 *
 * The model edits both with small operations instead of sending the
 * whole interface again. Nodes are found by id, not by JSON pointer: a
 * path like /children/2/children/0 breaks the moment something is
 * inserted above it, and models get such paths wrong. app.py applies the
 * same operations to the saved copy (_apply_ui_ops), so a reload shows
 * what the frame shows.
 */

export function clone(v) {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
}

/* ---- state paths: "/orders/0/total" ------------------------------------ */

function parts(path) {
  if (typeof path !== "string" || path === "" || path === "/") return [];
  return path.replace(/^\//, "").split("/").map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"));
}

export function getPath(obj, path) {
  let cur = obj;
  for (const p of parts(path)) {
    if (cur == null) return undefined;
    cur = cur[p];
  }
  return cur;
}

export function setPath(obj, path, value) {
  const ps = parts(path);
  if (!ps.length) return value;
  const root = Array.isArray(obj) ? obj.slice() : { ...(obj || {}) };
  let cur = root;
  for (let i = 0; i < ps.length - 1; i++) {
    const k = ps[i];
    const next = cur[k];
    cur[k] = Array.isArray(next) ? next.slice() : next && typeof next === "object" ? { ...next } : /^\d+$/.test(ps[i + 1]) ? [] : {};
    cur = cur[k];
  }
  const last = ps[ps.length - 1];
  if (value === undefined) {
    if (Array.isArray(cur)) cur.splice(Number(last), 1);
    else delete cur[last];
  } else {
    cur[last] = value;
  }
  return root;
}

/* ---- spec nodes ------------------------------------------------------- */

const STRUCTURAL = new Set(["type", "id", "children", "if", "props", "key"]);

/* A node as the model may write it - props inline or under "props" - in
   one shape: {type, id, children, if, props}. */
export function normalize(node) {
  if (node == null || typeof node !== "object") return null;
  if (Array.isArray(node)) return { type: "Fragment", props: {}, children: node.map(normalize).filter(Boolean) };
  const props = { ...(node.props || {}) };
  for (const [k, v] of Object.entries(node)) if (!STRUCTURAL.has(k)) props[k] = v;
  return {
    type: String(node.type || "Fragment"),
    id: node.id != null ? String(node.id) : undefined,
    if: node.if,
    props,
    children: Array.isArray(node.children) ? node.children.map(normalize).filter(Boolean) : [],
  };
}

function findWithParent(node, id, parent = null, index = -1) {
  if (!node) return null;
  if (node.id === id) return { node, parent, index };
  for (let i = 0; i < node.children.length; i++) {
    const hit = findWithParent(node.children[i], id, node, i);
    if (hit) return hit;
  }
  return null;
}

export function findNode(root, id) {
  const hit = findWithParent(root, id);
  return hit ? hit.node : null;
}

/* Applies a list of operations. Returns {spec, state, theme, errors}; an
   operation that cannot apply is skipped and reported, never fatal. */
export function applyOps(spec, state, ops, theme) {
  let s = clone(spec);
  let st = clone(state) || {};
  let th = theme;
  const errors = [];
  for (const op of Array.isArray(ops) ? ops : []) {
    try {
      switch (op && op.op) {
        case "set": st = setPath(st, op.path, op.value); break;
        case "merge": {
          const cur = getPath(st, op.path);
          st = setPath(st, op.path, { ...(cur && typeof cur === "object" ? cur : {}), ...(op.value || {}) });
          break;
        }
        case "delete": st = setPath(st, op.path, undefined); break;
        case "push": {
          const cur = getPath(st, op.path);
          st = setPath(st, op.path, [...(Array.isArray(cur) ? cur : []), op.value]);
          break;
        }
        case "update": {
          const hit = findWithParent(s, op.id);
          if (!hit) throw new Error(`no node with id "${op.id}"`);
          const merged = { ...hit.node.props };
          for (const [k, v] of Object.entries(op.props || {})) {
            if (v === null) delete merged[k]; else merged[k] = v;
          }
          hit.node.props = merged;
          if ("if" in op) hit.node.if = op.if;
          break;
        }
        case "replace": {
          const hit = findWithParent(s, op.id);
          if (!hit) throw new Error(`no node with id "${op.id}"`);
          const fresh = normalize(op.node);
          if (fresh && fresh.id === undefined) fresh.id = op.id;
          if (hit.parent) hit.parent.children[hit.index] = fresh; else s = fresh;
          break;
        }
        case "insert": {
          const node = normalize(op.node);
          if (op.before || op.after) {
            const hit = findWithParent(s, op.before || op.after);
            if (!hit || !hit.parent) throw new Error(`no node with id "${op.before || op.after}"`);
            hit.parent.children.splice(hit.index + (op.after ? 1 : 0), 0, node);
          } else {
            const parent = op.parent ? findNode(s, op.parent) : s;
            if (!parent) throw new Error(`no node with id "${op.parent}"`);
            const at = Number.isInteger(op.index) ? Math.max(0, Math.min(op.index, parent.children.length)) : parent.children.length;
            parent.children.splice(at, 0, node);
          }
          break;
        }
        case "remove": {
          const hit = findWithParent(s, op.id);
          if (!hit || !hit.parent) throw new Error(`no node with id "${op.id}"`);
          hit.parent.children.splice(hit.index, 1);
          break;
        }
        case "move": {
          const hit = findWithParent(s, op.id);
          if (!hit || !hit.parent) throw new Error(`no node with id "${op.id}"`);
          const target = op.parent ? findNode(s, op.parent) : hit.parent;
          if (!target) throw new Error(`no node with id "${op.parent}"`);
          hit.parent.children.splice(hit.index, 1);
          const at = Number.isInteger(op.index) ? Math.max(0, Math.min(op.index, target.children.length)) : target.children.length;
          target.children.splice(at, 0, hit.node);
          break;
        }
        case "root": s = normalize(op.node); break;
        case "theme": th = op.value === "light" ? "light" : "dark"; break;
        default: throw new Error(`unknown op "${op && op.op}"`);
      }
    } catch (e) {
      errors.push(String(e.message || e));
    }
  }
  return { spec: s, state: st, theme: th, errors };
}

/* ---- a tiny store with subscription ------------------------------------ */

export function createStore(initial) {
  let snapshot = initial;
  const subs = new Set();
  return {
    get: () => snapshot,
    set(next) {
      snapshot = typeof next === "function" ? next(snapshot) : next;
      subs.forEach((fn) => fn());
    },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}
