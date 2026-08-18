import Section from './Section'
import { CheckboxField, Field, MoneyField, SelectField, TextField } from './fields'
import type { Currency, DeckFormState, Intent } from '../types'

interface ConfigPanelProps {
  state: DeckFormState
  onChange: <K extends keyof DeckFormState>(key: K, value: DeckFormState[K]) => void
}

const INTENT_OPTIONS: { value: Intent; label: string }[] = [
  { value: 'auto', label: 'Auto (decide from text)' },
  { value: 'build', label: 'Build — from scratch' },
  { value: 'improve', label: 'Improve — upgrade weak cards' },
  { value: 'substitute', label: 'Substitute — swap specific cards' },
  { value: 'cut', label: 'Cut — trim down to size' },
]

export default function ConfigPanel({ state, onChange }: ConfigPanelProps) {
  return (
    <Section title="Configuration" subtitle="Commander, budget, and how the solver decides.">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Commander">
          <TextField
            value={state.commander}
            onChange={(e) => onChange('commander', e.target.value)}
            placeholder="e.g. Krenko, Mob Boss"
          />
        </Field>

        <Field label="Intent">
          <SelectField
            value={state.intent}
            onChange={(e) => onChange('intent', e.target.value as Intent)}
          >
            {INTENT_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </SelectField>
        </Field>

        <Field label="Max price per card">
          <MoneyField
            value={state.maxCardPrice}
            onChange={(v) => onChange('maxCardPrice', v)}
            placeholder="no limit"
            suffix={
              <select
                value={state.currency}
                onChange={(e) => onChange('currency', e.target.value as Currency)}
                className="bg-transparent outline-none"
              >
                <option value="usd">usd</option>
                <option value="eur">eur</option>
              </select>
            }
          />
        </Field>

        <Field label="Total deck budget">
          <MoneyField
            value={state.budgetCap}
            onChange={(v) => onChange('budgetCap', v)}
            placeholder="no limit"
            suffix={state.currency}
          />
        </Field>
      </div>

      <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 border-t border-surface-700 pt-4">
        <CheckboxField
          label="Only cards I own"
          checked={state.ownedOnly}
          onChange={(v) => onChange('ownedOnly', v)}
        />
        <CheckboxField
          label="Require complete deck"
          checked={state.requireComplete}
          onChange={(v) => onChange('requireComplete', v)}
        />
        <CheckboxField
          label="Owned copies cost 0"
          checked={state.ownedCostZero}
          onChange={(v) => onChange('ownedCostZero', v)}
        />
      </div>
    </Section>
  )
}
