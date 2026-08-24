import type { DeckCardEntry } from '../types'

let nextId = 1
function makeId(): string {
  nextId += 1
  return `c${nextId}`
}

// Moxfield/ManaBox exports append the printing after the name, e.g.
// "1 Sol Ring (C21) 263" or "Temple of Enlightenment (PTHB) 246p" -- strip it
// before it becomes part of the "card name" we send the backend. Left in place,
// none of these match anything in the Oracle catalog (which only knows plain
// names): the solver reads the whole list as unrecognized, strips nearly all
// of it as illegal, and silently refills every freed slot from generic search
// results -- from the user's side, "the whole deck changed" for no reason.
const PRINTING_SUFFIX_RE = /\s+\([A-Za-z0-9]{2,6}\)\s+[A-Za-z0-9-]+$/

/**
 * Accepts a pasted list like a ManaBox/Moxfield export:
 * "2x Sol Ring", "2 Sol Ring", "Sol Ring x2", "1 Sol Ring (C21) 263", or just
 * "Sol Ring" (qty 1). Empty lines are ignored.
 */
export function parseCardListText(text: string): DeckCardEntry[] {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

  const entries: DeckCardEntry[] = []
  for (const line of lines) {
    const leading = line.match(/^(\d+)\s*x?\s+(.+)$/i)
    const trailing = line.match(/^(.+?)\s*x\s*(\d+)$/i)
    let name = line
    let quantity = 1
    if (leading) {
      quantity = parseInt(leading[1], 10)
      name = leading[2].trim()
    } else if (trailing) {
      name = trailing[1].trim()
      quantity = parseInt(trailing[2], 10)
    }
    name = name.replace(PRINTING_SUFFIX_RE, '').trim()
    if (!name) continue
    entries.push({ id: makeId(), name, quantity: Math.max(1, quantity) })
  }
  return entries
}

export function totalQuantity(cards: DeckCardEntry[]): number {
  return cards.reduce((sum, c) => sum + (Number.isFinite(c.quantity) ? c.quantity : 0), 0)
}

/** Collapses the editable entry list into the {name: quantity} shape every
 * backend deck payload expects (blank names and non-positive quantities dropped,
 * duplicate names summed). */
export function cardsToDict(cards: DeckCardEntry[]): Record<string, number> {
  const dict: Record<string, number> = {}
  for (const card of cards) {
    const name = card.name.trim()
    if (!name || !(card.quantity > 0)) continue
    dict[name] = (dict[name] ?? 0) + card.quantity
  }
  return dict
}

/** Inverse of cardsToDict -- turns a saved deck's {name: quantity} back into
 * editable entries, for prefilling the Add/Edit deck form. */
export function dictToCards(cards: Record<string, number>): DeckCardEntry[] {
  return Object.entries(cards).map(([name, quantity]) => newCardEntry(name, quantity))
}

export function newCardEntry(name = '', quantity = 1): DeckCardEntry {
  return { id: makeId(), name, quantity }
}
