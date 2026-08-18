import { useState } from 'react'
import Section from './Section'
import CardChip from './CardChip'
import { newCardEntry, parseCardListText, totalQuantity } from '../lib/cardList'
import type { DeckCardEntry } from '../types'

interface DeckCardListProps {
  cards: DeckCardEntry[]
  onChange: (cards: DeckCardEntry[]) => void
}

export default function DeckCardList({ cards, onChange }: DeckCardListProps) {
  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteText, setPasteText] = useState('')
  const total = totalQuantity(cards)

  function updateCard(id: string, patch: Partial<DeckCardEntry>) {
    onChange(cards.map((c) => (c.id === id ? { ...c, ...patch } : c)))
  }

  function removeCard(id: string) {
    onChange(cards.filter((c) => c.id !== id))
  }

  function addEmptyCard() {
    onChange([...cards, newCardEntry()])
  }

  function applyPaste() {
    const parsed = parseCardListText(pasteText)
    if (parsed.length === 0) return
    onChange([...cards, ...parsed])
    setPasteText('')
    setPasteOpen(false)
  }

  return (
    <Section
      title="Current deck"
      subtitle="Optional — leave empty to build from scratch. Paste a list or add cards one by one."
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm text-surface-400">
          <span className="font-semibold text-surface-50">{total}</span> / 99 cards
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setPasteOpen((v) => !v)}
            className="rounded-lg border border-surface-600 px-3 py-1.5 text-sm text-surface-200 hover:border-accent-500 hover:text-surface-50"
          >
            Paste list
          </button>
          <button
            type="button"
            onClick={addEmptyCard}
            className="rounded-lg border border-surface-600 px-3 py-1.5 text-sm text-surface-200 hover:border-accent-500 hover:text-surface-50"
          >
            + Card
          </button>
        </div>
      </div>

      {pasteOpen && (
        <div className="mb-4 rounded-xl border border-surface-600 bg-surface-800 p-3">
          <textarea
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            placeholder={'2x Sol Ring\n1 Arcane Signet\nSmothering Tithe'}
            rows={5}
            className="w-full resize-y rounded-lg border border-surface-600 bg-surface-900 p-3 text-sm text-surface-50 placeholder:text-surface-400 outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30"
          />
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setPasteOpen(false)}
              className="rounded-lg px-3 py-1.5 text-sm text-surface-400 hover:text-surface-200"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={applyPaste}
              className="rounded-lg bg-accent-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-600"
            >
              Add
            </button>
          </div>
        </div>
      )}

      {cards.length === 0 ? (
        <p className="rounded-lg border border-dashed border-surface-600 py-8 text-center text-sm text-surface-400">
          No cards yet — that's fine, the solver fills from scratch.
        </p>
      ) : (
        <div className="max-h-72 overflow-y-auto pr-1">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {cards.map((card) => (
              <CardChip
                key={card.id}
                card={card}
                onChangeName={(name) => updateCard(card.id, { name })}
                onChangeQuantity={(quantity) => updateCard(card.id, { quantity })}
                onRemove={() => removeCard(card.id)}
              />
            ))}
          </div>
        </div>
      )}
    </Section>
  )
}
