// Mirrors the DeckState fields (src/deck_state.py) the user controls before
// hitting "play". `intent: "auto"` means "let the prompt text decide", same
// as the backend's infer_intent() already does today.

export type Intent = 'auto' | 'build' | 'improve' | 'substitute' | 'cut'
export type Currency = 'usd' | 'eur'
export type ManaStrategy = 'static' | 'hypergeometric'

export interface DeckCardEntry {
  id: string
  name: string
  quantity: number
}

export interface DeckFormState {
  query: string
  commander: string
  intent: Intent
  ownedOnly: boolean
  requireComplete: boolean
  ownedCostZero: boolean
  currency: Currency
  maxCardPrice: string
  priceCapExisting: boolean
  budgetCap: string
  manaStrategy: ManaStrategy
  cards: DeckCardEntry[]
  /** Saved deck names to build from as a fixed card pool (see fetchDeckPool) --
   * empty means no pool restriction, same as today. Mutually exclusive in
   * practice with ownedOnly: a pool is already narrower than "everything I own". */
  poolDeckNames: string[]
  /** Advanced option, default off. Only matters with a pool active and no
   * commander chosen: ranks the pool's legal commanders by color fit
   * (rules_validator.rank_commanders_by_pool_fit) instead of leaving the pick
   * purely to the Architect's training-data bias. No-op otherwise. */
  commanderByPoolFit: boolean
}

export const emptyFormState: DeckFormState = {
  query: '',
  commander: '',
  intent: 'auto',
  ownedOnly: false,
  requireComplete: false,
  ownedCostZero: true,
  currency: 'usd',
  maxCardPrice: '',
  priceCapExisting: false,
  budgetCap: '',
  manaStrategy: 'hypergeometric',
  cards: [],
  poolDeckNames: [],
  commanderByPoolFit: false,
}
