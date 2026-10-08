/* Inputs. Each takes "bind": a state path it reads and writes, so the
 * model sees what was chosen in the state and the choice survives a
 * reload. An on* action runs after the write. Built on Radix primitives
 * for keyboard and screen-reader behaviour that is right by default.
 */
import React, { useId, useState } from "react";
import { motion } from "motion/react";
import { Checkbox as RCheckbox, RadioGroup as RRadio, Select as RSelect, Slider as RSlider, Switch as RSwitch, ToggleGroup } from "radix-ui";
import { cn, formatValue, Icon } from "../lib.js";
import { Plain } from "../render.jsx";

const FIELD = "w-full rounded-lg border border-input bg-background/60 px-3 py-2 text-sm text-foreground shadow-xs outline-none transition-[border-color,box-shadow] placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-4 focus-visible:ring-ring/20 disabled:opacity-50";

function Field({ label, description, htmlFor, children, error }) {
  return (
    <div className="flex flex-col gap-1.5">
      {label && <label htmlFor={htmlFor} className="text-xs font-medium text-foreground">{label}</label>}
      {children}
      {description && !error && <p className="text-xs text-muted-foreground">{description}</p>}
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}

const VARIANTS = {
  primary: "bg-primary text-primary-foreground shadow-[0_6px_20px_-10px_var(--primary)] hover:brightness-110",
  secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
  outline: "border border-border bg-transparent text-foreground hover:bg-muted",
  ghost: "bg-transparent text-foreground hover:bg-muted",
  danger: "bg-danger text-white hover:brightness-110",
};

export function Button({ props, rt, scope }) {
  const { label, icon, iconRight, variant = "primary", size = "md", onClick, disabled, loading, full } = props;
  const sizes = { sm: "h-8 px-3 text-xs gap-1.5", md: "h-9 px-4 text-sm gap-2", lg: "h-11 px-5 text-sm gap-2" };
  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.97 }}
      transition={{ type: "spring", stiffness: 600, damping: 30 }}
      disabled={disabled || loading}
      onClick={() => rt.dispatch(onClick, scope)}
      className={cn(
        "inline-flex select-none items-center justify-center rounded-lg font-medium outline-none transition-[background,filter,box-shadow,color] focus-visible:ring-4 focus-visible:ring-ring/25 disabled:pointer-events-none disabled:opacity-50",
        sizes[size] || sizes.md,
        VARIANTS[variant] || VARIANTS.primary,
        full && "w-full",
      )}
    >
      {loading ? <Icon name="Loader2" size={15} className="animate-spin" /> : icon && <Icon name={icon} size={15} />}
      {label}
      {iconRight && <Icon name={iconRight} size={15} />}
    </motion.button>
  );
}

export function Input({ props, rt, scope }) {
  const id = useId();
  const { label, description, placeholder, bind, type = "text", onChange, onEnter, icon } = props;
  const value = bind ? rt.read(bind) ?? "" : undefined;
  return (
    <Field label={label} description={description} htmlFor={id}>
      <div className="relative">
        {icon && <Icon name={icon} size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />}
        <input
          id={id}
          type={["text", "number", "email", "date", "time", "search", "url", "tel", "password"].includes(type) ? type : "text"}
          className={cn(FIELD, icon && "pl-9")}
          placeholder={placeholder}
          value={value}
          onChange={(e) => {
            const v = type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value;
            if (bind) rt.write(bind, v);
            if (onChange) rt.dispatch(onChange, scope, v);
          }}
          onKeyDown={(e) => { if (e.key === "Enter" && onEnter) rt.dispatch(onEnter, scope, e.currentTarget.value); }}
        />
      </div>
    </Field>
  );
}

export function Textarea({ props, rt, scope }) {
  const id = useId();
  const { label, description, placeholder, bind, rows = 4, onChange } = props;
  return (
    <Field label={label} description={description} htmlFor={id}>
      <textarea
        id={id}
        rows={Math.max(2, Math.min(Number(rows) || 4, 20))}
        className={cn(FIELD, "resize-y")}
        placeholder={placeholder}
        value={bind ? rt.read(bind) ?? "" : undefined}
        onChange={(e) => { if (bind) rt.write(bind, e.target.value); if (onChange) rt.dispatch(onChange, scope, e.target.value); }}
      />
    </Field>
  );
}

const opts = (options) => (Array.isArray(options) ? options : []).map((o) => (typeof o === "object" && o ? { value: String(o.value ?? o.label), label: String(o.label ?? o.value), icon: o.icon, description: o.description } : { value: String(o), label: String(o) }));

export function Select({ props, rt, scope }) {
  const { label, description, placeholder = "Choose…", bind, options, onChange } = props;
  const list = opts(options);
  const value = bind ? rt.read(bind) : undefined;
  return (
    <Field label={label} description={description}>
      <RSelect.Root value={value != null ? String(value) : undefined} onValueChange={(v) => { if (bind) rt.write(bind, v); if (onChange) rt.dispatch(onChange, scope, v); }}>
        <RSelect.Trigger className={cn(FIELD, "flex items-center justify-between gap-2 text-left data-[placeholder]:text-muted-foreground")}>
          <RSelect.Value placeholder={placeholder} />
          <RSelect.Icon><Icon name="ChevronsUpDown" size={14} className="text-muted-foreground" /></RSelect.Icon>
        </RSelect.Trigger>
        <RSelect.Portal>
          <RSelect.Content position="popper" sideOffset={6} className="z-50 max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-xl data-[state=open]:animate-in">
            <RSelect.Viewport>
              {list.map((o) => (
                <RSelect.Item key={o.value} value={o.value} className="relative flex cursor-pointer select-none items-center gap-2 rounded-lg py-2 pl-8 pr-3 text-sm outline-none data-[highlighted]:bg-muted">
                  <RSelect.ItemIndicator className="absolute left-2.5"><Icon name="Check" size={14} className="text-primary" /></RSelect.ItemIndicator>
                  {o.icon && <Icon name={o.icon} size={14} className="text-muted-foreground" />}
                  <RSelect.ItemText>{o.label}</RSelect.ItemText>
                </RSelect.Item>
              ))}
            </RSelect.Viewport>
          </RSelect.Content>
        </RSelect.Portal>
      </RSelect.Root>
    </Field>
  );
}

export function Checkbox({ props, rt, scope }) {
  const id = useId();
  const { label, description, bind, onChange } = props;
  const checked = !!(bind && rt.read(bind));
  return (
    <div className="flex items-start gap-2.5">
      <RCheckbox.Root
        id={id}
        checked={checked}
        onCheckedChange={(v) => { if (bind) rt.write(bind, !!v); if (onChange) rt.dispatch(onChange, scope, !!v); }}
        className="mt-0.5 grid size-[18px] shrink-0 place-items-center rounded-[5px] border border-input bg-background outline-none transition-colors focus-visible:ring-4 focus-visible:ring-ring/25 data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground"
      >
        <RCheckbox.Indicator asChild>
          <motion.span initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: "spring", stiffness: 600, damping: 28 }}>
            <Icon name="Check" size={13} strokeWidth={3} />
          </motion.span>
        </RCheckbox.Indicator>
      </RCheckbox.Root>
      <label htmlFor={id} className="cursor-pointer select-none">
        <span className="block text-sm text-foreground">{label}</span>
        {description && <span className="block text-xs text-muted-foreground">{description}</span>}
      </label>
    </div>
  );
}

export function Switch({ props, rt, scope }) {
  const id = useId();
  const { label, description, bind, onChange } = props;
  const checked = !!(bind && rt.read(bind));
  return (
    <div className="flex items-center justify-between gap-4">
      <label htmlFor={id} className="cursor-pointer select-none">
        <span className="block text-sm font-medium text-foreground">{label}</span>
        {description && <span className="block text-xs text-muted-foreground">{description}</span>}
      </label>
      <RSwitch.Root
        id={id}
        checked={checked}
        onCheckedChange={(v) => { if (bind) rt.write(bind, v); if (onChange) rt.dispatch(onChange, scope, v); }}
        className="relative h-6 w-11 shrink-0 rounded-full bg-input outline-none transition-colors focus-visible:ring-4 focus-visible:ring-ring/25 data-[state=checked]:bg-primary"
      >
        <RSwitch.Thumb asChild>
          <motion.span layout transition={{ type: "spring", stiffness: 700, damping: 35 }} className={cn("block size-5 rounded-full bg-white shadow", checked ? "ml-[22px]" : "ml-0.5")} />
        </RSwitch.Thumb>
      </RSwitch.Root>
    </div>
  );
}

export function Slider({ props, rt, scope }) {
  const { label, bind, min = 0, max = 100, step = 1, format, currency, onChange } = props;
  const stored = bind ? rt.read(bind) : undefined;
  const [local, setLocal] = useState(null);
  const value = local ?? (typeof stored === "number" ? stored : Number(min));
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-foreground">{label}</span>
        <span className="gu-num rounded-md bg-muted px-1.5 py-0.5 font-medium text-foreground">{formatValue(value, format || "number", { currency })}</span>
      </div>
      <RSlider.Root
        className="relative flex h-5 w-full touch-none select-none items-center"
        min={Number(min)} max={Number(max)} step={Number(step) || 1}
        value={[value]}
        onValueChange={([v]) => { setLocal(v); if (bind) rt.write(bind, v); }}
        onValueCommit={([v]) => { setLocal(null); if (onChange) rt.dispatch(onChange, scope, v); }}
      >
        <RSlider.Track className="relative h-1.5 grow overflow-hidden rounded-full bg-muted">
          <RSlider.Range className="absolute h-full rounded-full bg-primary" />
        </RSlider.Track>
        <RSlider.Thumb aria-label={label || "Value"} className="block size-[18px] rounded-full border-2 border-primary bg-background shadow-md outline-none transition-transform hover:scale-110 focus-visible:ring-4 focus-visible:ring-ring/30" />
      </RSlider.Root>
    </div>
  );
}

/* A row of options, one chosen - the shadcn "segmented control". */
export function Segmented({ props, rt, scope }) {
  const { label, bind, options, onChange, full } = props;
  const list = opts(options);
  const value = bind ? rt.read(bind) : undefined;
  const groupId = useId();
  return (
    <Field label={label}>
      <ToggleGroup.Root
        type="single"
        value={value != null ? String(value) : ""}
        onValueChange={(v) => { if (!v) return; if (bind) rt.write(bind, v); if (onChange) rt.dispatch(onChange, scope, v); }}
        className={cn("inline-flex rounded-lg bg-muted p-1", full && "flex w-full")}
      >
        {list.map((o) => {
          const on = String(value) === o.value;
          return (
            <ToggleGroup.Item
              key={o.value}
              value={o.value}
              className={cn("relative flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring", on ? "text-foreground" : "text-muted-foreground hover:text-foreground")}
            >
              {on && <motion.span layoutId={`seg-${groupId}`} className="absolute inset-0 rounded-md bg-card shadow-sm" transition={{ type: "spring", stiffness: 500, damping: 36 }} />}
              <span className="relative z-10 flex items-center gap-1.5">{o.icon && <Icon name={o.icon} size={14} />}{o.label}</span>
            </ToggleGroup.Item>
          );
        })}
      </ToggleGroup.Root>
    </Field>
  );
}

/* Options as cards, for choices that need a line of explanation. */
export function RadioGroup({ props, rt, scope }) {
  const { label, bind, options, onChange, columns } = props;
  const list = opts(options);
  const value = bind ? rt.read(bind) : undefined;
  return (
    <Field label={label}>
      <RRadio.Root
        value={value != null ? String(value) : undefined}
        onValueChange={(v) => { if (bind) rt.write(bind, v); if (onChange) rt.dispatch(onChange, scope, v); }}
        className="grid gap-2"
        style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${columns ? Math.floor(100 / Number(columns)) - 2 + "%" : "150px"}, 1fr))` }}
      >
        {list.map((o) => {
          const on = String(value) === o.value;
          return (
            <RRadio.Item key={o.value} value={o.value} className={cn("flex items-start gap-3 rounded-xl border p-3 text-left outline-none transition-all focus-visible:ring-4 focus-visible:ring-ring/25", on ? "border-primary bg-primary/8 shadow-[inset_0_0_0_1px_var(--primary)]" : "border-border hover:border-foreground/20 hover:bg-muted/40")}>
              {o.icon && <span className={cn("grid size-8 shrink-0 place-items-center rounded-lg", on ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}><Icon name={o.icon} size={15} /></span>}
              <span className="min-w-0">
                <span className="block text-sm font-medium text-foreground">{o.label}</span>
                {o.description && <span className="block text-xs text-muted-foreground">{o.description}</span>}
              </span>
            </RRadio.Item>
          );
        })}
      </RRadio.Root>
    </Field>
  );
}

/* Fields with a submit button. Enter in a text field submits too. */
export function Form({ node, props, rt, scope }) {
  const { submitLabel = "Submit", onSubmit, cancelLabel, onCancel, icon } = props;
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => { e.preventDefault(); rt.dispatch(onSubmit || { submit: true }, scope); }}
    >
      <Plain node={node} />
      <div className="flex items-center justify-end gap-2 pt-1">
        {cancelLabel && <Button props={{ label: cancelLabel, variant: "ghost", onClick: onCancel }} rt={rt} scope={scope} />}
        <motion.button whileTap={{ scale: 0.97 }} type="submit" className={cn("inline-flex h-9 items-center gap-2 rounded-lg px-4 text-sm font-medium outline-none focus-visible:ring-4 focus-visible:ring-ring/25", VARIANTS.primary)}>
          {icon && <Icon name={icon} size={15} />}{submitLabel}
        </motion.button>
      </div>
    </form>
  );
}
