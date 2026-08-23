import { useState } from 'react'
import CommanderField from './CommanderField'
import DeckCardList from './DeckCardList'
import Section from './Section'
import { TextField, Field } from './fields'
import { saveDeck } from '../lib/api'
import type { DeckValidation, SavedDeckDetail } from '../lib/api'
import { cardsToDict, dictToCards } from '../lib/cardList'
import type { DeckCardEntry } from '../types'

interface AddDeckFormProps {
  /** When set, the form edits this deck instead of creating a new one: name
   * is locked (it's the location key -- see decks.py) and the other fields
   * start prefilled from its current cards. */
  initialDeck?: SavedDeckDetail
  onSaved: () => void
  onCancel: () => void
}

// Validation errors here never block the save (see save_deck in
// service/handlers/decks.py) -- they're surfaced as read-only warnings after
// the fact, not a gate the user has to clear first.
function ValidationSummary({ validation }: { validation: DeckValidation }) {
  // A commander that's missing or unresolvable in the catalog short-circuits
  // CommanderValidator before it ever gets to cards: the backend returns just
  // {error, valid, complete}, none of the arrays below. Handle that shape
  // first instead of assuming every field is always present.
  if (validation.error) {
    return (
      <p className="rounded-lg border border-red-900 bg-red-950/30 px-3 py-2 text-sm text-red-300">
        Saved, but not validated — {validation.error}
      </p>
    )
  }

  const warnings = validation.warnings ?? []
  const groups: [string, string[]][] = [
    ['Commander', validation.commander_errors ?? []],
    ['Unknown cards', validation.unknown_cards ?? []],
    ['Color identity', validation.color_errors ?? []],
    ['Singleton', validation.singleton_errors ?? []],
    ['Format legality', validation.format_errors ?? []],
    ['Size', validation.size_errors ?? []],
  ]
  const problems = groups.filter(([, items]) => items.length > 0)

  if (validation.valid && warnings.length === 0) {
    return (
      <p className="rounded-lg border border-emerald-800 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-300">
        Saved — {validation.slot_count}/{validation.target_slots} cards, no issues found.
      </p>
    )
  }

  return (
    <div className="rounded-lg border border-amber-800 bg-amber-950/30 px-3 py-2 text-sm text-amber-300">
      <p className="font-medium">
        Saved with {problems.length + (warnings.length > 0 ? 1 : 0)} warning
        {problems.length === 1 && warnings.length === 0 ? '' : 's'} — {validation.slot_count}/
        {validation.target_slots} cards.
      </p>
      <ul className="mt-1.5 list-inside list-disc space-y-0.5 text-xs text-amber-200">
        {problems.map(([label, items]) => (
          <li key={label}>
            {label}: {items.join(', ')}
          </li>
        ))}
        {warnings.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
    </div>
  )
}

export default function AddDeckForm({ initialDeck, onSaved, onCancel }: AddDeckFormProps) {
  const isEditing = initialDeck != null
  const [name, setName] = useState(initialDeck?.name ?? '')
  const [commander, setCommander] = useState(initialDeck?.commander ?? '')
  const [cards, setCards] = useState<DeckCardEntry[]>(() => (initialDeck ? dictToCards(initialDeck.cards) : []))
  const [saving, setSaving] = useState(false)
  const [validation, setValidation] = useState<DeckValidation | null>(null)
  const [error, setError] = useState<string | undefined>()

  const canSave = name.trim().length > 0 && commander.trim().length > 0 && !saving

  async function handleSave() {
    if (!canSave) return
    setSaving(true)
    setError(undefined)
    setValidation(null)
    try {
      const result = await saveDeck(name.trim(), commander.trim(), cardsToDict(cards))
      setValidation(result.validation)
      // Saving always succeeds regardless of what validation says (see
      // save_deck), so the parent's deck list is stale the moment this
      // resolves -- refresh it now, but keep the form open so the warnings
      // above stay visible until the user clicks Close.
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section
      title={initialDeck ? `Edit deck — ${initialDeck.name}` : 'Add deck'}
      subtitle="Commander plus its cards — quantity defaults to 1 if you leave it off."
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field
          label="Deck name"
          hint={isEditing ? "Can't be changed after creation." : "How you'll refer to this deck later, e.g. 'kotis'."}
        >
          <TextField
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. kotis"
            disabled={isEditing}
          />
        </Field>
        <CommanderField value={commander} onChange={setCommander} />
      </div>

      <div className="mt-4">
        <DeckCardList cards={cards} onChange={setCards} />
      </div>

      {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
      {validation && (
        <div className="mt-3">
          <ValidationSummary validation={validation} />
        </div>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-3 py-1.5 text-sm text-surface-400 hover:text-surface-200"
        >
          {validation ? 'Close' : 'Cancel'}
        </button>
        <button
          type="button"
          disabled={!canSave}
          onClick={handleSave}
          className="rounded-lg bg-accent-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-600 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save deck'}
        </button>
      </div>
    </Section>
  )
}
