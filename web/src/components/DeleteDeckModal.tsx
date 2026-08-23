interface DeleteDeckModalProps {
  /** Deck name for a single delete, or e.g. "3 decks" for a bulk one. */
  subject: string
  busy: boolean
  onCancel: () => void
  onConfirm: (removeCards: boolean) => void
}

// A plain confirm() can't offer a choice, and this deck delete has one that
// matters: cards are physical possession (see decks.py), so "delete the deck"
// is ambiguous between "unassign its cards" and "I don't own these anymore."
export default function DeleteDeckModal({ subject, busy, onCancel, onConfirm }: DeleteDeckModalProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={busy ? undefined : onCancel}
    >
      <div
        className="w-full max-w-sm rounded-2xl border border-surface-700 bg-surface-900 p-4 shadow-2xl shadow-black/50"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-surface-50">Delete {subject}?</h3>
        <p className="mt-1.5 text-sm text-surface-400">Choose what happens to its cards in your collection.</p>

        <div className="mt-4 flex flex-col gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onConfirm(false)}
            className="rounded-lg border border-surface-600 px-3 py-2 text-left text-sm hover:border-accent-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="block font-medium text-surface-100">Delete deck only</span>
            <span className="block text-xs text-surface-400">Cards go back to the free pool — still in your collection.</span>
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onConfirm(true)}
            className="rounded-lg border border-red-900 px-3 py-2 text-left text-sm hover:border-red-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="block font-medium text-red-300">Delete deck and remove its cards</span>
            <span className="block text-xs text-red-400/80">Also removes these cards from your collection entirely.</span>
          </button>
        </div>

        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-lg px-3 py-1.5 text-sm text-surface-400 hover:text-surface-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? 'Working…' : 'Cancel'}
          </button>
        </div>
      </div>
    </div>
  )
}
