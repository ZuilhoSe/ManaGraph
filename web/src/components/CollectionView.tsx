import { useEffect, useMemo, useState } from 'react'
import Section from './Section'
import { API_BASE, fetchInventory, type InventoryCard } from '../lib/api'

type Status = 'loading' | 'ready' | 'error'

export default function CollectionView() {
  const [status, setStatus] = useState<Status>('loading')
  const [cards, setCards] = useState<InventoryCard[]>([])
  const [filter, setFilter] = useState('')

  useEffect(() => {
    let cancelled = false
    setStatus('loading')
    fetchInventory()
      .then((data) => {
        if (cancelled) return
        setCards(data)
        setStatus('ready')
      })
      .catch(() => {
        if (!cancelled) setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return cards
    return cards.filter((c) => c.name.toLowerCase().includes(q))
  }, [cards, filter])

  const totalCopies = useMemo(() => cards.reduce((sum, c) => sum + c.quantity, 0), [cards])

  if (status === 'error') {
    return (
      <Section title="Collection">
        <p className="text-sm text-surface-300">
          Couldn't reach the API at <code className="text-accent-400">{API_BASE}</code>.
        </p>
        <p className="mt-2 text-xs text-surface-400">
          Start it from the project root: <code className="text-surface-200">cd src &amp;&amp; uvicorn service.api:app --reload --port 8000</code>
        </p>
      </Section>
    )
  }

  return (
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

      {status === 'loading' ? (
        <p className="py-8 text-center text-sm text-surface-400">Loading collection...</p>
      ) : filtered.length === 0 ? (
        <p className="py-8 text-center text-sm text-surface-400">No cards match "{filter}".</p>
      ) : (
        <div className="max-h-96 overflow-y-auto rounded-lg border border-surface-700">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-surface-800 text-xs text-surface-400 uppercase">
              <tr>
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
  )
}
