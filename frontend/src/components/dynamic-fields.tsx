"use client";

import { useEffect, useState } from "react";
import { csrfFetch } from "@/lib/api";

export type DynamicFieldDefinition = {
  id: string;
  form_key: string;
  key: string;
  label: string;
  field_type: "text" | "textarea" | "number" | "date" | "select" | "checkbox";
  required: boolean;
  options: string[];
  placeholder: string;
  help_text: string;
  sort_order: number;
};

export function readDynamicFields(data: FormData, fields: DynamicFieldDefinition[]) {
  return Object.fromEntries(fields.map((field) => [
    field.key,
    field.field_type === "checkbox" ? data.get(`custom_${field.key}`) === "on" : data.get(`custom_${field.key}`),
  ]));
}

export function DynamicFields({ fields, values = {} }: { fields: DynamicFieldDefinition[]; values?: Record<string, unknown> }) {
  if (!fields.length) return null;
  return <>
    <div className="form-section-title">University fields</div>
    {fields.map((field) => {
      const name = `custom_${field.key}`;
      if (field.field_type === "checkbox") return <label className="field check-field" key={field.id}><input name={name} type="checkbox" defaultChecked={Boolean(values[field.key])} /><span>{field.label}</span>{field.help_text && <small>{field.help_text}</small>}</label>;
      return <label className={`field ${field.field_type === "textarea" ? "full-field" : ""}`} key={field.id}>
        <span>{field.label}</span>
        {field.field_type === "textarea" ? <textarea name={name} rows={3} defaultValue={String(values[field.key] ?? "")} placeholder={field.placeholder} required={field.required} />
          : field.field_type === "select" ? <select name={name} defaultValue={String(values[field.key] ?? "")} required={field.required}><option value="">Select</option>{field.options.map((option) => <option value={option} key={option}>{option}</option>)}</select>
          : <input name={name} type={field.field_type} defaultValue={String(values[field.key] ?? "")} placeholder={field.placeholder} required={field.required} />}
        {field.help_text && <small>{field.help_text}</small>}
      </label>;
    })}
  </>;
}

export function useDynamicFields(formKey: string) {
  const [fields, setFields] = useState<DynamicFieldDefinition[]>([]);

  useEffect(() => {
    let active = true;
    void csrfFetch(`/api/v1/enterprise/form-fields?form_key=${encodeURIComponent(formKey)}`)
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail || "University fields could not be loaded");
        if (active) setFields(body.fields || []);
      })
      .catch(() => { if (active) setFields([]); });
    return () => { active = false; };
  }, [formKey]);

  return fields;
}
