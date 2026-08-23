import type { DeckFormState, Intent, ManaStrategy } from '../types'
import { newCardEntry } from './cardList'

const VALID_INTENTS: Intent[] = ['build', 'improve', 'substitute', 'cut']
const VALID_MANA_STRATEGIES: ManaStrategy[] = ['static', 'hypergeometric']

export function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function exportFilename(form: DeckFormState): string {
  const slug = (form.commander || 'deck')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return `managraph_${slug || 'deck'}.json`
}

/**
 * Reverse of buildPayload() -- same {query, deck} shape produced there and by
 * `python src/main_agent.py --export`, so a file can move freely between the
 * web UI and a hand-run CLI session. Unknown/extra deck fields (identity,
 * candidate_pool, slot_count, ...) are ignored rather than rejected, since
 * they're backend-computed and not part of the form.
 */
export function parseImportedPayload(raw: unknown): DeckFormState {
  if (!raw || typeof raw !== 'object') {
    throw new Error('Not a valid ManaGraph JSON file.')
  }
  const payload = raw as Record<string, unknown>
  const deck = (payload.deck && typeof payload.deck === 'object' ? payload.deck : {}) as Record<
    string,
    unknown
  >
  const cardsRaw = (deck.cards && typeof deck.cards === 'object' ? deck.cards : {}) as Record<
    string,
    unknown
  >
  const cards = Object.entries(cardsRaw)
    .filter(([name, qty]) => name.trim() && Number(qty) > 0)
    .map(([name, qty]) => newCardEntry(name, Number(qty)))

  const intent = typeof deck.intent === 'string' && VALID_INTENTS.includes(deck.intent as Intent)
    ? (deck.intent as Intent)
    : 'auto'

  const manaStrategy =
    typeof deck.mana_strategy === 'string' &&
    VALID_MANA_STRATEGIES.includes(deck.mana_strategy as ManaStrategy)
      ? (deck.mana_strategy as ManaStrategy)
      : 'hypergeometric'

  return {
    query: typeof payload.query === 'string' ? payload.query : '',
    commander: typeof deck.commander === 'string' ? deck.commander : '',
    intent,
    ownedOnly: Boolean(deck.owned_only),
    requireComplete: Boolean(deck.require_complete),
    ownedCostZero: deck.owned_cost_zero !== false,
    currency: deck.currency === 'eur' ? 'eur' : 'usd',
    maxCardPrice: typeof deck.max_card_price === 'number' ? String(deck.max_card_price) : '',
    priceCapExisting: deck.price_cap_new_only === false,
    budgetCap: typeof deck.budget_cap === 'number' ? String(deck.budget_cap) : '',
    manaStrategy,
    cards,
  }
}
