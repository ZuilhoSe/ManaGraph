import { Fragment, useEffect, useState } from 'react'
import Section from './Section'
import { CheckboxField, SelectField } from './fields'
import { analyzeDeck, fetchDeck, fetchDeckPool, fetchDecks } from '../lib/api'
import type { CardScoreBreakdown, DeckAnalysis, SavedDeck, SavedDeckDetail } from '../lib/api'
import { VALID_ARCHETYPES } from '../types'
import type { Archetype } from '../types'

type AnalysisStatus = 'idle' | 'loading' | 'error'
type DecksStatus = 'loading' | 'ready' | 'error'

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toFixed(3)
}

function TotalBadge({ total }: { total: number }) {
  const positive = total >= 0
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ${
        positive ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'
      }`}
    >
      {total >= 0 ? '+' : ''}
      {total.toFixed(3)}
    </span>
  )
}

function RoleChips({ roles }: { roles: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {roles.map((r) => (
        <span
          key={r}
          className="rounded-full border border-surface-600 px-1.5 py-0.5 text-[10px] tracking-wide text-surface-300 uppercase"
        >
          {r}
        </span>
      ))}
    </div>
  )
}

// Every field score_breakdown() computes but that doesn't fit the compact
// table row -- the actual "memory of calculation" for one card.
function CardDetail({ card }: { card: CardScoreBreakdown }) {
  const rows: [string, string][] = [
    ['synergy', fmt(card.synergy)],
    ['geometry (commander cos.)', fmt(card.geometry)],
    ['jaccard fallback', fmt(card.jaccard)],
    ['role_score', fmt(card.role_score)],
    ['tribe', fmt(card.tribe)],
    ['token_align', fmt(card.token_align)],
    ['redundancy', card.redundancy_with ? `${fmt(card.redundancy)} (vs. ${card.redundancy_with})` : fmt(card.redundancy)],
    ['curve_bonus', fmt(card.curve_bonus)],
    ['curve_penalty', fmt(card.curve_penalty)],
    ['land_bonus', fmt(card.land_bonus)],
    ['mana_bonus', fmt(card.mana_bonus)],
    ['shape (total)', fmt(card.shape)],
    ['value (budget)', fmt(card.value)],
  ]
  const roleBonusEntries = Object.entries(card.role_bonuses)
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-1 bg-surface-950/40 px-4 py-3 text-xs sm:grid-cols-3">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-2">
          <span className="text-surface-400">{label}</span>
          <span className="tabular-nums text-surface-100">{value}</span>
        </div>
      ))}
      {roleBonusEntries.length > 0 && (
        <div className="col-span-full mt-1 border-t border-surface-800 pt-2">
          <span className="text-surface-400">role_bonuses: </span>
          <span className="text-surface-100">
            {roleBonusEntries.map(([role, bonus]) => `${role}=${bonus.toFixed(2)}`).join(', ')}
          </span>
        </div>
      )}
      {!card.eligible && card.reason && (
        <div className="col-span-full mt-1 text-amber-300">Ineligible: {card.reason}</div>
      )}
    </div>
  )
}

function CardTable({ cards }: { cards: CardScoreBreakdown[] }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  if (cards.length === 0) {
    return <p className="py-6 text-center text-sm text-surface-400">No cards to show.</p>
  }
  return (
    <div className="max-h-[32rem] overflow-y-auto rounded-lg border border-surface-700">
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-surface-800 text-xs text-surface-400 uppercase">
          <tr>
            <th className="px-3 py-2 font-medium">Name</th>
            <th className="px-3 py-2 font-medium">Roles</th>
            <th className="px-3 py-2 text-right font-medium">Total</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-surface-700">
          {cards.map((card) => (
            <Fragment key={card.name}>
              <tr
                onClick={() => setExpanded((prev) => (prev === card.name ? null : card.name))}
                className={`cursor-pointer text-surface-100 hover:bg-surface-800/60 ${
                  card.eligible === false ? 'opacity-50' : ''
                }`}
              >
                <td className="px-3 py-2 font-medium text-surface-50">
                  {card.name}
                  {card.eligible === false && (
                    <span className="ml-2 rounded-full border border-surface-600 px-1.5 py-0.5 text-[10px] text-surface-400 uppercase">
                      ineligible
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <RoleChips roles={card.roles} />
                </td>
                <td className="px-3 py-2 text-right">
                  <TotalBadge total={card.total} />
                </td>
              </tr>
              {expanded === card.name && (
                <tr>
                  <td colSpan={3} className="p-0">
                    <CardDetail card={card} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function AnalysisView() {
  const [decks, setDecks] = useState<SavedDeck[]>([])
  const [decksStatus, setDecksStatus] = useState<DecksStatus>('loading')
  const [selectedDeck, setSelectedDeck] = useState('')
  const [deckDetail, setDeckDetail] = useState<SavedDeckDetail | null>(null)
  const [archetypeOverride, setArchetypeOverride] = useState<Archetype | ''>('')
  const [poolDeckNames, setPoolDeckNames] = useState<string[]>([])
  const [poolCards, setPoolCards] = useState<Record<string, number> | null>(null)
  const [analysis, setAnalysis] = useState<DeckAnalysis | null>(null)
  const [status, setStatus] = useState<AnalysisStatus>('idle')

  useEffect(() => {
    fetchDecks()
      .then((data) => {
        setDecks(data)
        setDecksStatus('ready')
      })
      .catch(() => setDecksStatus('error'))
  }, [])

  // Compare-against-pool decks reuse the same union-of-physical-cards
  // endpoint the Build tab's pool-restricted runs already use.
  useEffect(() => {
    if (poolDeckNames.length === 0) {
      setPoolCards(null)
      return
    }
    let cancelled = false
    fetchDeckPool(poolDeckNames)
      .then((info) => {
        if (!cancelled) setPoolCards(info.pool)
      })
      .catch(() => {
        if (!cancelled) setPoolCards(null)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(poolDeckNames)])

  // Deck detail only depends on which deck is selected -- fetched once per
  // selection, not re-fetched every time the archetype or pool comparison
  // changes below.
  useEffect(() => {
    if (!selectedDeck) {
      setDeckDetail(null)
      setAnalysis(null)
      return
    }
    let cancelled = false
    fetchDeck(selectedDeck)
      .then((detail) => {
        if (!cancelled) setDeckDetail(detail)
      })
      .catch(() => {
        if (!cancelled) setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [selectedDeck])

  useEffect(() => {
    if (!deckDetail) return
    let cancelled = false
    setStatus('loading')
    analyzeDeck(deckDetail.commander, deckDetail.cards, archetypeOverride || undefined, poolCards || undefined)
      .then((result) => {
        if (cancelled) return
        setAnalysis(result)
        setStatus('idle')
      })
      .catch(() => {
        if (!cancelled) setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [deckDetail, archetypeOverride, poolCards])

  function togglePoolDeck(name: string) {
    setPoolDeckNames((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]))
  }

  return (
    <div className="flex flex-col gap-4">
      <Section title="Analysis" subtitle="Per-card role and score breakdown for a saved deck.">
        {decksStatus === 'loading' ? (
          <p className="py-4 text-center text-sm text-surface-400">Loading decks...</p>
        ) : decksStatus === 'error' ? (
          <p className="py-4 text-center text-sm text-surface-400">Couldn't load saved decks.</p>
        ) : decks.length === 0 ? (
          <p className="rounded-lg border border-dashed border-surface-600 py-6 text-center text-sm text-surface-400">
            No decks saved yet — add one in the Collection tab first.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-surface-200">Deck</span>
              <SelectField value={selectedDeck} onChange={(e) => setSelectedDeck(e.target.value)}>
                <option value="">Choose a deck…</option>
                {decks.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name} ({d.commander})
                  </option>
                ))}
              </SelectField>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-surface-200">Archetype</span>
              <SelectField
                value={archetypeOverride}
                onChange={(e) => setArchetypeOverride(e.target.value as Archetype | '')}
              >
                <option value="">
                  {analysis ? `Auto (${analysis.archetype})` : 'Auto (inferred from commander)'}
                </option>
                {VALID_ARCHETYPES.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </SelectField>
            </label>
          </div>
        )}

        {selectedDeck && (
          <div className="mt-4 border-t border-surface-700 pt-4">
            <p className="text-sm font-medium text-surface-200">Compare against a pool</p>
            <p className="mt-0.5 text-xs text-surface-400">
              Union the physical cards of these decks and score every one not already in the deck above —
              the "why didn't this card make it" view.
            </p>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5">
              {decks.map((d) => (
                <CheckboxField
                  key={d.name}
                  label={d.name}
                  checked={poolDeckNames.includes(d.name)}
                  onChange={() => togglePoolDeck(d.name)}
                />
              ))}
            </div>
          </div>
        )}
      </Section>

      {status === 'loading' && (
        <Section title="Deck">
          <p className="py-4 text-center text-sm text-surface-400">Scoring…</p>
        </Section>
      )}
      {status === 'error' && (
        <Section title="Deck">
          <p className="py-4 text-center text-sm text-surface-400">Couldn't compute the breakdown.</p>
        </Section>
      )}
      {status === 'idle' && analysis && (
        <>
          <Section title="Deck" subtitle={`${analysis.deck_cards.length} cards · archetype: ${analysis.archetype}`}>
            <CardTable cards={analysis.deck_cards} />
          </Section>
          {analysis.pool_cards && (
            <Section title="Pool candidates not in deck" subtitle={`${analysis.pool_cards.length} cards`}>
              <CardTable cards={analysis.pool_cards} />
            </Section>
          )}
        </>
      )}
    </div>
  )
}
