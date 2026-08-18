import type { DeckCardEntry } from '../types'

interface CardChipProps {
  card: DeckCardEntry
  onChangeName: (name: string) => void
  onChangeQuantity: (quantity: number) => void
  onRemove: () => void
}

// A single card slot. Text + quantity for now; once card images are wired up
// (via Scryfall), only the inside of this component needs to change — the
// grid that renders it (DeckCardList) stays the same.
export default function CardChip({ card, onChangeName, onChangeQuantity, onRemove }: CardChipProps) {
  return (
    <div className="group flex items-center gap-2 rounded-lg border border-surface-600 bg-surface-800 py-1.5 pr-1.5 pl-3">
      <input
        type="number"
        min={1}
        value={card.quantity}
        onChange={(e) => onChangeQuantity(Math.max(1, Number(e.target.value) || 1))}
        className="w-9 shrink-0 rounded bg-surface-700 py-0.5 text-center text-sm text-surface-50 outline-none focus:ring-2 focus:ring-accent-500/40"
      />
      <input
        type="text"
        value={card.name}
        onChange={(e) => onChangeName(e.target.value)}
        placeholder="Card name"
        className="min-w-0 flex-1 truncate bg-transparent text-sm text-surface-50 placeholder:text-surface-400 outline-none"
      />
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${card.name || 'card'}`}
        className="shrink-0 rounded-md px-2 py-1 text-surface-400 opacity-0 transition group-hover:opacity-100 hover:bg-surface-700 hover:text-surface-50"
      >
        ×
      </button>
    </div>
  )
}
