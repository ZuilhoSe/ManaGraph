import type { DeckCardEntry } from '../types'

let nextId = 1
function makeId(): string {
  nextId += 1
  return `c${nextId}`
}

/**
 * Accepts a pasted list like a ManaBox/Moxfield export:
 * "2x Sol Ring", "2 Sol Ring", "Sol Ring x2", or just "Sol Ring" (qty 1).
 * Empty lines are ignored.
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
    if (!name) continue
    entries.push({ id: makeId(), name, quantity: Math.max(1, quantity) })
  }
  return entries
}

export function totalQuantity(cards: DeckCardEntry[]): number {
  return cards.reduce((sum, c) => sum + (Number.isFinite(c.quantity) ? c.quantity : 0), 0)
}

export function newCardEntry(name = '', quantity = 1): DeckCardEntry {
  return { id: makeId(), name, quantity }
}
