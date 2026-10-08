/* What a spec may contain. build.mjs writes this to static/genui/
 * catalog.json; app.py validates specs against it and puts it in the
 * model's brief, so the components, the validator and the model's
 * instructions cannot drift apart. Keep each line short: it is read by
 * the model on every turn that builds an interface.
 */
export const CATALOG = {
  // layout
  Page: { group: "layout", children: true, props: "title, subtitle, eyebrow, icon, badge, badgeTone - the top of most interfaces" },
  Stack: { group: "layout", children: true, props: "gap(0-8) - vertical" },
  Row: { group: "layout", children: true, props: "gap, align(start|center|end|stretch), justify(start|center|end|between), wrap, grow" },
  Grid: { group: "layout", children: true, props: "min(px, narrowest column; default 180), cols(max columns), gap - responsive" },
  Card: { group: "layout", children: true, props: "title, description, icon, variant(default|outline|glass|accent|ghost), padding(none|sm|md|lg), hover, gap" },
  Section: { group: "layout", children: true, props: "title, description, gap" },
  Divider: { group: "layout", children: false, props: "label" },
  Spacer: { group: "layout", children: false, props: "size(px)" },
  Repeat: { group: "layout", children: true, props: "items(binding to a list), layout(stack|grid|row), min, gap, empty(text) - children are the template; read the item with {\"$item\": \"field\"} or \"{{item.field}}\"" },
  // display
  Heading: { group: "display", children: false, props: "text, level(1-4), eyebrow, subtitle" },
  Text: { group: "display", children: false, props: "text (**bold**, `code`), tone(default|muted|primary|success|warning|danger), size(xs|sm|md|lg|xl), weight, align" },
  Badge: { group: "display", children: false, props: "text, tone(default|primary|success|warning|danger|info|muted), icon, pulse" },
  Icon: { group: "display", children: false, props: "name(Lucide), size, tone, chip" },
  KPI: { group: "display", children: false, props: "label, value, format(number|currency|percent|compact|decimal), currency(ISO), decimals, delta(fraction, e.g. 0.12), deltaLabel, icon, trend(list of numbers -> sparkline), loading, tone" },
  Stat: { group: "display", children: false, props: "label, value, format, currency - small inline figure" },
  Progress: { group: "display", children: false, props: "label, value(0-100), tone, showValue, caption" },
  Avatar: { group: "display", children: false, props: "name, size" },
  List: { group: "display", children: false, props: "items([{id, title, description, icon|avatar, badge, badgeTone, value, format}]), onSelect, dividers" },
  Table: { group: "display", children: false, props: "columns([{key, label, align, format(number|currency|percent|compact|badge), currency, tones}]), rows(list), onSelect, sortable, compact, empty" },
  Steps: { group: "display", children: false, props: "items([{title, description, status(done|active|todo|error)}])" },
  Callout: { group: "display", children: false, props: "tone(info|success|warning|danger|primary), title, text, icon" },
  EmptyState: { group: "display", children: false, props: "icon, title, description" },
  Skeleton: { group: "display", children: false, props: "lines, height" },
  Kbd: { group: "display", children: false, props: "text" },
  Image: { group: "display", children: false, props: "src(data: URL only), alt, height, rounded" },
  // inputs (bind = state path read and written)
  Button: { group: "input", children: false, props: "label, icon, iconRight, variant(primary|secondary|outline|ghost|danger), size(sm|md|lg), onClick, disabled, loading, full" },
  Input: { group: "input", children: false, props: "label, placeholder, bind, type(text|number|email|date|time|search|url|tel), icon, description, onChange, onEnter" },
  Textarea: { group: "input", children: false, props: "label, placeholder, bind, rows, description, onChange" },
  Select: { group: "input", children: false, props: "label, options(strings or [{value, label, icon}]), bind, placeholder, description, onChange" },
  Checkbox: { group: "input", children: false, props: "label, description, bind, onChange" },
  Switch: { group: "input", children: false, props: "label, description, bind, onChange" },
  Slider: { group: "input", children: false, props: "label, bind, min, max, step, format, currency, onChange (on release)" },
  Segmented: { group: "input", children: false, props: "label, options, bind, onChange, full - one of a few" },
  RadioGroup: { group: "input", children: false, props: "label, options([{value, label, description, icon}]), bind, columns, onChange - choice cards" },
  Form: { group: "input", children: true, props: "submitLabel, onSubmit (default: submit), cancelLabel, onCancel, icon - children are fields" },
  // structure and overlays
  Tabs: { group: "overlay", children: true, props: "variant(underline|pills), bind, onChange - children are Tab nodes" },
  Tab: { group: "overlay", children: true, props: "id, label, icon, count" },
  Accordion: { group: "overlay", children: true, props: "defaultOpen(item id) - children are AccordionItem nodes" },
  AccordionItem: { group: "overlay", children: true, props: "id, title, icon" },
  Dialog: { group: "overlay", children: true, props: "id (required), title, description, onClose - opened with {\"open\": id}" },
  Sheet: { group: "overlay", children: true, props: "id (required), side(right|left), title, description, onClose - a drawer, opened with {\"open\": id}" },
  Tooltip: { group: "overlay", children: true, props: "text - wraps one child" },
  Fragment: { group: "layout", children: true, props: "(groups children without a box)" },
};

/* What the on* props can do. One action, or a list run in order. */
export const ACTIONS = {
  set: "{\"set\": {\"/path\": value}} - value may be \"$event\" (the input's value) or a binding",
  toggle: "{\"toggle\": \"/path\"}",
  push: "{\"push\": {\"/list\": value}}",
  remove: "{\"remove\": {\"/list\": index}} - index may be {\"$index\": true} inside a Repeat",
  open: "{\"open\": \"dialog-or-sheet-id\"}",
  close: "{\"close\": \"dialog-or-sheet-id\"}",
  toast: "{\"toast\": \"Saved\"} or {\"toast\": {\"title\", \"description\", \"tone\"}}",
  emit: "{\"emit\": \"event_name\", \"data\": {...}} - tells you what the user did; when you wait_for_user it is the answer",
  notify: "{\"notify\": \"Generate 3 more ideas\"} - sends this as the user's next message to you, starting a turn",
  submit: "{\"submit\": true} - answers your wait with the whole state",
  display: "{\"display\": \"expanded\"} - asks the page to show the interface full width",
};
