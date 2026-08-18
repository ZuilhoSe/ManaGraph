import type { ReactNode } from 'react'

interface FieldProps {
  label: string
  hint?: string
  children: ReactNode
}

export function Field({ label, hint, children }: FieldProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium text-surface-200">{label}</span>
      {children}
      {hint && <span className="text-xs text-surface-400">{hint}</span>}
    </label>
  )
}

const inputClass =
  'w-full rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-surface-50 placeholder:text-surface-400 outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30'

export function TextField(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input type="text" {...props} className={inputClass} />
}

export function NumberField(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input type="number" inputMode="decimal" {...props} className={inputClass} />
}

export function SelectField(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={inputClass} />
}

interface CheckboxFieldProps {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}

export function CheckboxField({ label, checked, onChange }: CheckboxFieldProps) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-surface-100 select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 shrink-0 rounded border-surface-600 bg-surface-800 accent-accent-500"
      />
      {label}
    </label>
  )
}

interface MoneyFieldProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  /** Content to the right of the value: an editable currency <select>, or fixed text. */
  suffix: ReactNode
}

export function MoneyField({ value, onChange, placeholder, suffix }: MoneyFieldProps) {
  return (
    <div className="flex overflow-hidden rounded-lg border border-surface-600 bg-surface-800 focus-within:border-accent-500 focus-within:ring-2 focus-within:ring-accent-500/30">
      <input
        type="number"
        inputMode="decimal"
        min={0}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-0 min-w-0 flex-1 bg-transparent px-3 py-2 text-sm text-surface-50 placeholder:text-surface-400 outline-none"
      />
      <div className="flex shrink-0 items-center border-l border-surface-600 px-2 text-sm text-surface-300 uppercase">
        {suffix}
      </div>
    </div>
  )
}
