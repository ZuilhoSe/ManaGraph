import type { DeckFormState } from '../types'
import { cardsToDict } from './cardList'

// Matches the shape that today gets passed by hand to initial_graph_state(query, deck)
// in src/main_agent.py — this replaces the hardcoded query in __main__.
//
// `pool` is the resolved card_pool dict (see fetchDeckPool), fetched by the
// caller (App.tsx) before calling this -- kept out of this function so it
// stays a pure, synchronous mapping of form state to payload. Only meaningful
// when state.poolDeckNames is non-empty; a pool restriction always wins over
// "Only cards I own" since it's already the narrower constraint.
export function buildPayload(state: DeckFormState, pool?: Record<string, number>) {
  const poolActive = state.poolDeckNames.length > 0 && pool != null
  const deck: Record<string, unknown> = {
    commander: state.commander.trim(),
    cards: cardsToDict(state.cards),
    owned_only: poolActive ? false : state.ownedOnly,
    require_complete: state.requireComplete,
    owned_cost_zero: state.ownedCostZero,
    // Inverted on purpose: the checkbox asks "also cap cards already in the
    // deck", the backend field asks "cap new cards only" (its safer default).
    price_cap_new_only: !state.priceCapExisting,
    currency: state.currency,
    mana_strategy: state.manaStrategy,
  }

  if (poolActive) {
    deck.pool_only = true
    deck.card_pool = pool
    if (state.commanderByPoolFit) deck.commander_by_pool_fit = true
  }

  if (state.intent !== 'auto') deck.intent = state.intent
  if (state.maxCardPrice.trim() !== '') deck.max_card_price = Number(state.maxCardPrice)
  if (state.budgetCap.trim() !== '') deck.budget_cap = Number(state.budgetCap)

  return {
    query: state.query.trim(),
    deck,
  }
}
