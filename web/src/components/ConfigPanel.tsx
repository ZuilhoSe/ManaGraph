import { useState } from 'react'
import CommanderField from './CommanderField'
import Section from './Section'
import { CheckboxField, Field, MoneyField, SelectField } from './fields'
import type { Currency, DeckFormState, Intent, ManaStrategy } from '../types'

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

const MANA_STRATEGY_OPTIONS: { value: ManaStrategy; label: string }[] = [
  { value: 'hypergeometric', label: 'Hypergeometric — curve/pip-weighted target' },
  { value: 'static', label: 'Static — fixed quota by color count' },
]

export default function ConfigPanel({ state, onChange }: ConfigPanelProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false)

  return (
    <Section title="Configuration" subtitle="Commander, budget, and how the solver decides.">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <CommanderField value={state.commander} onChange={(v) => onChange('commander', v)} />

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
          {state.maxCardPrice.trim() !== '' && (
            <label className="mt-1.5 flex items-center gap-1.5 text-xs text-surface-400">
              <input
                type="checkbox"
                checked={state.priceCapExisting}
                onChange={(e) => onChange('priceCapExisting', e.target.checked)}
                className="h-3.5 w-3.5 rounded border-surface-600 bg-surface-800"
              />
              Also cap cards already in the deck
            </label>
          )}
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

      <div className="mt-4 border-t border-surface-700 pt-4">
        <button
          type="button"
          onClick={() => setAdvancedOpen((prev) => !prev)}
          className="text-sm font-medium text-surface-300 hover:text-surface-50"
        >
          {advancedOpen ? '▾' : '▸'} Advanced options
        </button>

        {advancedOpen && (
          <div className="mt-3 max-w-xs">
            <Field
              label="Mana calculator"
              hint="How land/color-source targets are computed for this deck."
            >
              <SelectField
                value={state.manaStrategy}
                onChange={(e) => onChange('manaStrategy', e.target.value as ManaStrategy)}
              >
                {MANA_STRATEGY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </SelectField>
            </Field>
          </div>
        )}
      </div>
    </Section>
  )
}
