import { useEffect, useState } from 'react'
import CommanderField from './CommanderField'
import Section from './Section'
import { CheckboxField, Field, MoneyField, SelectField } from './fields'
import { fetchDeckPool, fetchDecks } from '../lib/api'
import type { DeckPool, SavedDeck } from '../lib/api'
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
  const [savedDecks, setSavedDecks] = useState<SavedDeck[]>([])
  const [pool, setPool] = useState<DeckPool | null>(null)
  const [poolStatus, setPoolStatus] = useState<'idle' | 'loading' | 'error'>('idle')

  useEffect(() => {
    fetchDecks()
      .then(setSavedDecks)
      .catch(() => {})
  }, [])

  const poolActive = state.poolDeckNames.length > 0

  useEffect(() => {
    if (!poolActive) {
      setPool(null)
      setPoolStatus('idle')
      return
    }
    let cancelled = false
    setPoolStatus('loading')
    fetchDeckPool(state.poolDeckNames)
      .then((info) => {
        if (cancelled) return
        setPool(info)
        setPoolStatus('idle')
        // Drop a commander that no longer qualifies (deck deselected, or it
        // was never pool-eligible) instead of silently submitting a run that
        // rules_validator would just reject as "not in the selected pool".
        if (state.commander && !info.commanders.includes(state.commander)) {
          onChange('commander', '')
        }
      })
      .catch(() => {
        if (!cancelled) setPoolStatus('error')
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-fetch only on deck selection change, not every commander edit
  }, [JSON.stringify(state.poolDeckNames)])

  function togglePoolDeck(name: string) {
    const next = state.poolDeckNames.includes(name)
      ? state.poolDeckNames.filter((n) => n !== name)
      : [...state.poolDeckNames, name]
    onChange('poolDeckNames', next)
  }

  return (
    <Section title="Configuration" subtitle="Commander, budget, and how the solver decides.">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {poolActive ? (
          <Field label="Commander" hint="Limited to commander-legal cards in the selected pool.">
            <SelectField
              value={state.commander}
              onChange={(e) => onChange('commander', e.target.value)}
            >
              <option value="">
                {poolStatus === 'loading' ? 'Loading pool…' : 'Choose a commander…'}
              </option>
              {(pool?.commanders ?? []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </SelectField>
          </Field>
        ) : (
          <CommanderField value={state.commander} onChange={(v) => onChange('commander', v)} />
        )}

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
        {!poolActive && (
          <CheckboxField
            label="Only cards I own"
            checked={state.ownedOnly}
            onChange={(v) => onChange('ownedOnly', v)}
          />
        )}
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
        <p className="text-sm font-medium text-surface-200">Build from a card pool</p>
        <p className="mt-0.5 text-xs text-surface-400">
          Restrict the build to cards physically in these decks — a hard limit, not a preference.
          The commander comes from the pool too.
        </p>
        {savedDecks.length === 0 ? (
          <p className="mt-2 text-xs text-surface-500">No saved decks yet — add some in the Collection tab.</p>
        ) : (
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5">
            {savedDecks.map((d) => (
              <CheckboxField
                key={d.name}
                label={`${d.name} (${d.card_count})`}
                checked={state.poolDeckNames.includes(d.name)}
                onChange={() => togglePoolDeck(d.name)}
              />
            ))}
          </div>
        )}
        {poolActive && (
          <p className="mt-2 text-xs text-surface-400">
            {poolStatus === 'loading' && 'Loading pool…'}
            {poolStatus === 'error' && <span className="text-red-400">Couldn't load the pool.</span>}
            {poolStatus === 'idle' && pool && (
              <>
                {Object.keys(pool.pool).length} unique cards in the pool.
                {pool.commanders.length === 0 && (
                  <span className="text-amber-300"> No commander-legal card found in this pool.</span>
                )}
              </>
            )}
          </p>
        )}
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
          <div className="mt-3 max-w-xs space-y-3">
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

            <CheckboxField
              label="Decide commander by pool color fit"
              checked={state.commanderByPoolFit}
              onChange={(v) => onChange('commanderByPoolFit', v)}
            />
            <p className="-mt-2 text-xs text-surface-400">
              Only applies when building from a card pool with no commander chosen: ranks the
              pool's legal commanders by how well the pool's color mix supports each
              one, instead of leaving the pick purely to the AI's judgment.
            </p>
          </div>
        )}
      </div>
    </Section>
  )
}
