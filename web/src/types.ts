// Mirrors the DeckState fields (src/deck_state.py) the user controls before
// hitting "play". `intent: "auto"` means "let the prompt text decide", same
// as the backend's infer_intent() already does today.

export type Intent = 'auto' | 'build' | 'improve' | 'substitute' | 'cut'
export type Currency = 'usd' | 'eur'

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
  cards: DeckCardEntry[]
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
  cards: [],
}
