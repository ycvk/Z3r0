import { Input, InputNumber, Select, Switch, TextArea } from "../../shared/ui/semi";
import type { ReactNode } from "react";
import { cx } from "../../shared/lib/className";

export type NumberFieldWidth = "compact" | "standard" | "wide" | "fill";

type FieldBase = { label: string; hint?: ReactNode; full?: boolean };

type FieldProps = FieldBase & (
  | { kind: "text"; value: string; maxLength?: number; password?: boolean; placeholder?: string; onChange: (value: string) => void }
  | {
      kind: "number";
      value: number;
      min?: number;
      max?: number;
      step?: number;
      width?: NumberFieldWidth;
      onChange: (value: number) => void;
    }
  | {
      kind: "optional-number";
      value: number | null;
      min?: number;
      max?: number;
      step?: number;
      placeholder?: string;
      onChange: (value: number | null) => void;
    }
  | { kind: "toggle"; value: boolean; onChange: (value: boolean) => void }
  | { kind: "json"; value: string; placeholder?: string; onChange: (value: string) => void }
  | {
      kind: "select";
      value: string | null;
      options: { label: string; value: string }[];
      placeholder?: string;
      clearable?: boolean;
      onChange: (value: string | null) => void;
    }
);

export function Field(props: FieldProps) {
  const className = cx(
    "field",
    props.full && "full",
    props.kind === "toggle" && "switch-field",
    props.kind === "number" && "number-field",
    props.kind === "number" && `number-field-${props.width ?? "standard"}`,
    props.kind === "optional-number" && "number-field number-field-fill",
  );
  return (
    <label className={className}>
      <span>{props.label}</span>
      {props.kind === "text" ? (
        <Input
          type={props.password ? "password" : "text"}
          value={props.value}
          maxLength={props.maxLength}
          placeholder={props.placeholder}
          autoComplete={props.password ? "new-password" : "off"}
          onChange={props.onChange}
        />
      ) : props.kind === "number" ? (
        <InputNumber
          value={props.value}
          min={props.min}
          max={props.max}
          step={props.step}
          onChange={(next) => typeof next === "number" && props.onChange(next)}
        />
      ) : props.kind === "optional-number" ? (
        <InputNumber
          value={props.value ?? ""}
          min={props.min}
          max={props.max}
          step={props.step}
          placeholder={props.placeholder}
          onChange={(next) => props.onChange(typeof next === "number" ? next : null)}
        />
      ) : props.kind === "toggle" ? (
        <Switch checked={props.value} onChange={props.onChange} aria-label={props.label} />
      ) : props.kind === "json" ? (
        <TextArea
          value={props.value}
          placeholder={props.placeholder}
          autosize={{ minRows: 1, maxRows: 6 }}
          onChange={props.onChange}
        />
      ) : (
        <Select
          value={props.value ?? undefined}
          optionList={props.options}
          placeholder={props.placeholder}
          showClear={props.clearable}
          onChange={(next) => props.onChange(typeof next === "string" ? next : null)}
        />
      )}
      {props.hint ? <small className="field-hint">{props.hint}</small> : null}
    </label>
  );
}
