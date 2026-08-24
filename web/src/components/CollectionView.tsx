import { useEffect, useMemo, useState } from 'react'
import AddDeckForm from './AddDeckForm'
import DeleteDeckModal from './DeleteDeckModal'
import Section from './Section'
import {
  API_BASE,
  deleteDeck,
  deleteInventoryCard,
  fetchDeck,
  fetchDecks,
  fetchInventory,
  syncMissingCards,
  type InventoryCard,
  type SavedDeck,
  type SavedDeckDetail,
} from '../lib/api'

type Status = 'loading' | 'ready' | 'error'
type FormMode = 'closed' | 'add' | 'edit'

interface SavedDecksSectionProps {
  /** Deck saves/edits/deletes change what's in the collection (the commander,
   * cards moved to/from the free pool, ...) -- called after each so the
   * Collection table below doesn't sit stale until an unrelated remount. */
  onDeckChange: () => void
}

function SavedDecksSection({ onDeckChange }: SavedDecksSectionProps) {
  const [status, setStatus] = useState<Status>('loading')
  const [decks, setDecks] = useState<SavedDeck[]>([])
  const [formMode, setFormMode] = useState<FormMode>('closed')
  const [editingDeck, setEditingDeck] = useState<SavedDeckDetail | null>(null)
  const [editLoading, setEditLoading] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [pendingDelete, setPendingDelete] = useState<string[] | null>(null)
  const [busyAction, setBusyAction] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)

  function load() {
    setStatus('loading')
    fetchDecks()
      .then((data) => {
        setDecks(data)
        setStatus('ready')
      })
      .catch(() => setStatus('error'))
  }

  useEffect(load, [])

  function refreshAll() {
    load()
    onDeckChange()
  }

  function closeForm() {
    setFormMode('closed')
    setEditingDeck(null)
  }

  async function startEdit(name: string) {
    setFormMode('edit')
    setEditLoading(true)
    try {
      setEditingDeck(await fetchDeck(name))
    } catch {
      setFormMode('closed')
    } finally {
      setEditLoading(false)
    }
  }

  function toggleSelected(name: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  function selectAll() {
    setSelected(new Set(decks.map((d) => d.name)))
  }

  function clearSelection() {
    setSelected(new Set())
  }

  async function confirmDelete(removeCards: boolean) {
    const names = pendingDelete
    if (!names || names.length === 0) return
    setBusyAction(true)
    try {
      await Promise.all(names.map((name) => deleteDeck(name, removeCards)))
      setPendingDelete(null)
      setSelected((prev) => {
        const next = new Set(prev)
        for (const name of names) next.delete(name)
        return next
      })
      refreshAll()
    } catch {
      // Best-effort: leave the modal open so the user can see it didn't take and retry.
    } finally {
      setBusyAction(false)
    }
  }

  async function handleSyncSelected() {
    const names = Array.from(selected)
    if (names.length === 0) return
    setBusyAction(true)
    setSyncMessage(null)
    try {
      const result = await syncMissingCards(names)
      setSyncMessage(
        result.added.length === 0
          ? 'Nothing missing — every selected deck already has its commander in the collection.'
          : `Added to the collection: ${result.added.map((a) => `${a.card} (${a.deck})`).join(', ')}.`,
      )
      onDeckChange()
    } catch (err) {
      setSyncMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyAction(false)
    }
  }

  return (
    <Section
      title="Saved decks"
      subtitle={status === 'ready' ? `${decks.length} deck${decks.length === 1 ? '' : 's'}` : undefined}
      actions={
        formMode === 'closed' && (
          <button
            type="button"
            onClick={() => setFormMode('add')}
            className="rounded-lg border border-surface-600 px-3 py-1.5 text-sm text-surface-200 hover:border-accent-500 hover:text-surface-50"
          >
            + Add deck
          </button>
        )
      }
    >
      {formMode === 'add' ? (
        <AddDeckForm onSaved={refreshAll} onCancel={closeForm} />
      ) : formMode === 'edit' ? (
        editLoading || !editingDeck ? (
          <p className="py-4 text-center text-sm text-surface-400">Loading deck...</p>
        ) : (
          <AddDeckForm initialDeck={editingDeck} onSaved={refreshAll} onCancel={closeForm} />
        )
      ) : status === 'loading' ? (
        <p className="py-4 text-center text-sm text-surface-400">Loading decks...</p>
      ) : status === 'error' ? (
        <p className="py-4 text-center text-sm text-surface-400">Couldn't load saved decks.</p>
      ) : decks.length === 0 ? (
        <p className="rounded-lg border border-dashed border-surface-600 py-6 text-center text-sm text-surface-400">
          No decks saved yet.
        </p>
      ) : (
        <>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-3">
              <button type="button" onClick={selectAll} className="font-medium text-surface-300 hover:text-surface-50">
                Select all
              </button>
              {selected.size > 0 && (
                <button
                  type="button"
                  onClick={clearSelection}
                  className="font-medium text-surface-400 hover:text-surface-200"
                >
                  Clear selection
                </button>
              )}
              {selected.size > 0 && <span className="text-surface-400">{selected.size} selected</span>}
            </div>
            {selected.size > 0 && (
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={handleSyncSelected}
                  disabled={busyAction}
                  className="rounded-lg border border-surface-600 px-3 py-1.5 font-medium text-surface-200 hover:border-accent-500 hover:text-surface-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Add cards not in the collection
                </button>
                <button
                  type="button"
                  onClick={() => setPendingDelete(Array.from(selected))}
                  disabled={busyAction}
                  className="rounded-lg border border-red-900 px-3 py-1.5 font-medium text-red-300 hover:border-red-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Delete selected ({selected.size})
                </button>
              </div>
            )}
          </div>

          {syncMessage && <p className="mb-2 text-xs text-surface-400">{syncMessage}</p>}

          <div className="divide-y divide-surface-700 rounded-lg border border-surface-700">
            {decks.map((deck) => (
              <div key={deck.name} className="flex items-center justify-between px-3 py-2 text-sm">
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={selected.has(deck.name)}
                    onChange={() => toggleSelected(deck.name)}
                    className="h-4 w-4 rounded border-surface-600 bg-surface-800 accent-accent-500"
                  />
                  <div>
                    <span className="font-medium text-surface-50">{deck.name}</span>
                    <span className="ml-2 text-surface-400">{deck.commander}</span>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-surface-400">{deck.card_count} cards</span>
                  <button
                    type="button"
                    onClick={() => startEdit(deck.name)}
                    className="text-xs font-medium text-surface-400 hover:text-surface-50"
                  >
                    Edit
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {pendingDelete && (
        <DeleteDeckModal
          subject={pendingDelete.length === 1 ? `"${pendingDelete[0]}"` : `${pendingDelete.length} decks`}
          busy={busyAction}
          onCancel={() => setPendingDelete(null)}
          onConfirm={confirmDelete}
        />
      )}
    </Section>
  )
}

export default function CollectionView() {
  const [status, setStatus] = useState<Status>('loading')
  const [cards, setCards] = useState<InventoryCard[]>([])
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)

  function load() {
    setStatus('loading')
    fetchInventory()
      .then((data) => {
        setCards(data)
        setStatus('ready')
      })
      .catch(() => setStatus('error'))
  }

  useEffect(load, [])

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return cards
    return cards.filter((c) => c.name.toLowerCase().includes(q))
  }, [cards, filter])

  const totalCopies = useMemo(() => cards.reduce((sum, c) => sum + c.quantity, 0), [cards])

  function toggleSelected(name: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  // Selects exactly what's currently visible under the filter, replacing
  // whatever was selected before -- not a union with a prior filter's picks.
  function selectAllFiltered() {
    setSelected(new Set(filtered.map((c) => c.name)))
  }

  function clearSelection() {
    setSelected(new Set())
  }

  async function handleDeleteSelected() {
    const names = Array.from(selected)
    if (names.length === 0) return
    if (
      !window.confirm(
        `Remove ${names.length} card${names.length === 1 ? '' : 's'} from your collection entirely? This can't be undone.`,
      )
    ) {
      return
    }
    setBulkDeleting(true)
    try {
      await Promise.all(names.map((name) => deleteInventoryCard(name)))
    } finally {
      // Reload regardless of partial failure so the list reflects whatever
      // actually got removed, then drop the selection either way.
      setSelected(new Set())
      setBulkDeleting(false)
      load()
    }
  }

  if (status === 'error') {
    return (
      <div className="flex flex-col gap-4">
        <SavedDecksSection onDeckChange={load} />
        <Section title="Collection">
          <p className="text-sm text-surface-300">
            Couldn't reach the API at <code className="text-accent-400">{API_BASE}</code>.
          </p>
          <p className="mt-2 text-xs text-surface-400">
            Start it from the project root: <code className="text-surface-200">cd src &amp;&amp; uvicorn service.api:app --reload --port 8000</code>
          </p>
        </Section>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <SavedDecksSection />
      <Section
        title="Collection"
        subtitle={status === 'ready' ? `${cards.length} unique cards · ${totalCopies} copies` : 'Loading...'}
      >
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by name..."
          className="mb-3 w-full rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-surface-50 placeholder:text-surface-400 outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30"
        />

        {status === 'ready' && cards.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={selectAllFiltered}
                className="font-medium text-surface-300 hover:text-surface-50"
              >
                Select all
              </button>
              {selected.size > 0 && (
                <button type="button" onClick={clearSelection} className="font-medium text-surface-400 hover:text-surface-200">
                  Clear selection
                </button>
              )}
              {selected.size > 0 && <span className="text-surface-400">{selected.size} selected</span>}
            </div>
            {selected.size > 0 && (
              <button
                type="button"
                onClick={handleDeleteSelected}
                disabled={bulkDeleting}
                className="rounded-lg border border-red-900 px-3 py-1.5 font-medium text-red-300 hover:border-red-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {bulkDeleting ? 'Deleting…' : `Delete selected (${selected.size})`}
              </button>
            )}
          </div>
        )}

        {status === 'loading' ? (
          <p className="py-8 text-center text-sm text-surface-400">Loading collection...</p>
        ) : filtered.length === 0 ? (
          <p className="py-8 text-center text-sm text-surface-400">No cards match "{filter}".</p>
        ) : (
          <div className="max-h-96 overflow-y-auto rounded-lg border border-surface-700">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-surface-800 text-xs text-surface-400 uppercase">
                <tr>
                  <th className="w-8 px-3 py-2" />
                  <th className="px-3 py-2 font-medium">Name</th>
                  <th className="px-3 py-2 font-medium">Type</th>
                  <th className="px-3 py-2 font-medium">Mana</th>
                  <th className="px-3 py-2 text-right font-medium">Qty</th>
                  <th className="px-3 py-2 font-medium">Locations</th>
                  <th className="px-3 py-2 text-right font-medium">Price</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-700">
                {filtered.map((card) => (
                  <tr key={card.name} className="text-surface-100">
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        checked={selected.has(card.name)}
                        onChange={() => toggleSelected(card.name)}
                        className="h-4 w-4 rounded border-surface-600 bg-surface-800 accent-accent-500"
                      />
                    </td>
                    <td className="px-3 py-2 font-medium text-surface-50">{card.name}</td>
                    <td className="px-3 py-2 text-surface-400">{card.type_line}</td>
                    <td className="px-3 py-2 text-surface-400">{card.mana_cost}</td>
                    <td className="px-3 py-2 text-right">{card.quantity}</td>
                    <td className="px-3 py-2 text-surface-400">
                      {Object.entries(card.allocations)
                        .map(([loc, qty]) => `${loc} (${qty})`)
                        .join(', ')}
                    </td>
                    <td className="px-3 py-2 text-right text-surface-400">
                      {card.price_usd != null ? `$${card.price_usd.toFixed(2)}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}
