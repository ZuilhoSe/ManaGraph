import type { DeckFormState } from '../types'

// Matches the shape that today gets passed by hand to initial_graph_state(query, deck)
// in src/main_agent.py — this replaces the hardcoded query in __main__.
export function buildPayload(state: DeckFormState) {
  const cards: Record<string, number> = {}
  for (const card of state.cards) {
    const name = card.name.trim()
    if (!name || !(card.quantity > 0)) continue
    cards[name] = (cards[name] ?? 0) + card.quantity
  }

  const deck: Record<string, unknown> = {
    commander: state.commander.trim(),
    cards,
    owned_only: state.ownedOnly,
    require_complete: state.requireComplete,
    owned_cost_zero: state.ownedCostZero,
    // Inverted on purpose: the checkbox asks "also cap cards already in the
    // deck", the backend field asks "cap new cards only" (its safer default).
    price_cap_new_only: !state.priceCapExisting,
    currency: state.currency,
  }

  if (state.intent !== 'auto') deck.intent = state.intent
  if (state.maxCardPrice.trim() !== '') deck.max_card_price = Number(state.maxCardPrice)
  if (state.budgetCap.trim() !== '') deck.budget_cap = Number(state.budgetCap)

  return {
    query: state.query.trim(),
    deck,
  }
}
