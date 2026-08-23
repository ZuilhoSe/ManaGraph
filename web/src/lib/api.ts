export const API_BASE = 'http://localhost:8000'

export interface InventoryCard {
  name: string
  quantity: number
  allocations: Record<string, number>
  type_line: string
  mana_cost: string
  cmc: number | null
  price_usd: number | null
  price_eur: number | null
}

export async function fetchInventory(): Promise<InventoryCard[]> {
  const res = await fetch(`${API_BASE}/api/inventory`)
  if (!res.ok) throw new Error(`Inventory request failed (${res.status})`)
  const data = await res.json()
  return data.cards as InventoryCard[]
}

// Removes the card from every location (free pool and any saved decks), not
// just one -- see delete_inventory_card in service/handlers/inventory.py.
export async function deleteInventoryCard(name: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/inventory/${encodeURIComponent(name)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`Delete card failed (${res.status})`)
}

export interface CommanderCandidate {
  name: string
  type_line: string
  identity: string[]
}

export async function searchCommanders(query: string, signal?: AbortSignal): Promise<CommanderCandidate[]> {
  const trimmed = query.trim()
  if (trimmed.length < 2) return []
  const res = await fetch(`${API_BASE}/api/commanders?q=${encodeURIComponent(trimmed)}`, { signal })
  if (!res.ok) throw new Error(`Commander search failed (${res.status})`)
  const data = await res.json()
  return data.commanders as CommanderCandidate[]
}

export interface SavedDeck {
  name: string
  commander: string
  card_count: number
  created_at: string
}

export async function fetchDecks(): Promise<SavedDeck[]> {
  const res = await fetch(`${API_BASE}/api/decks`)
  if (!res.ok) throw new Error(`Decks request failed (${res.status})`)
  const data = await res.json()
  return data.decks as SavedDeck[]
}

export interface SavedDeckDetail {
  name: string
  commander: string
  cards: Record<string, number>
}

export async function fetchDeck(name: string): Promise<SavedDeckDetail> {
  const res = await fetch(`${API_BASE}/api/decks/${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`Deck request failed (${res.status})`)
  return res.json()
}

// removeCards=false (default): cards go back to the free pool, still owned.
// removeCards=true: cards are dropped from the collection entirely.
export async function deleteDeck(name: string, removeCards = false): Promise<void> {
  const query = removeCards ? '?remove_cards=true' : ''
  const res = await fetch(`${API_BASE}/api/decks/${encodeURIComponent(name)}${query}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`Delete deck failed (${res.status})`)
}

export interface DeckValidation {
  valid: boolean
  complete: boolean
  // CommanderValidator.validate_deck_state (rules_validator.py) returns a
  // much smaller shape -- just {error, valid, complete} -- when the commander
  // itself doesn't resolve (missing or not found in the catalog), before it
  // ever gets to checking cards. Every field below is genuinely absent then,
  // not just empty.
  error?: string
  slot_count?: number
  target_slots?: number
  commander_errors?: string[]
  color_errors?: string[]
  singleton_errors?: string[]
  format_errors?: string[]
  size_errors?: string[]
  owned_errors?: string[]
  price_errors?: string[]
  unknown_cards?: string[]
  warnings?: string[]
}

export interface SaveDeckResult {
  saved: boolean
  name: string
  commander: string
  validation: DeckValidation
}

export interface SyncDecksResult {
  added: { deck: string; card: string }[]
}

// Adds each deck's commander to the collection if it's missing there (older
// decks saved before save_deck started including the commander automatically
// -- see add_missing_cards in service/handlers/decks.py). Can't recover a
// non-commander card removed from the collection after the fact.
export async function syncMissingCards(names: string[]): Promise<SyncDecksResult> {
  const res = await fetch(`${API_BASE}/api/decks/sync`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names }),
  })
  if (!res.ok) throw new Error(`Sync decks failed (${res.status})`)
  return res.json()
}

// Always resolves with the save result, even when validation reports
// problems -- saving never blocks on validation (see rules_validator.py's
// CommanderValidator, reused as-is on the backend). A non-2xx response here
// means the request itself was malformed (e.g. missing name), not that the
// deck was illegal.
export async function saveDeck(name: string, commander: string, cards: Record<string, number>): Promise<SaveDeckResult> {
  const res = await fetch(`${API_BASE}/api/decks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, commander, cards }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `Save deck failed (${res.status})`)
  }
  return res.json()
}

export type RunNode = 'architect' | 'inventory' | 'solver' | 'supervisor'

export interface DeckDiffEntry {
  name: string
  quantity: number
}

export interface DeckDiff {
  removed: DeckDiffEntry[]
  added: DeckDiffEntry[]
  commander_changed: { from: string; to: string } | null
  removed_count: number
  added_count: number
}

export interface DeckRunEvent {
  type: 'run_id' | 'start' | 'log' | 'node_start' | 'node' | 'error' | 'done' | 'cancelled'
  /** Which node (architect/inventory/solver/supervisor) this event is about.
   *  On an "error"/"cancelled" event, this is the node that was running when it stopped. */
  node?: RunNode
  agent?: string
  text?: string
  deck?: Record<string, unknown>
  validation?: { valid: boolean; error?: string; warnings?: string[] }
  supervisor_decision?: string
  solver_report?: unknown
  message?: string
  deck_diff?: DeckDiff
  /** Only on the first ("run_id") event -- pass this to cancelRun() to stop the run server-side. */
  run_id?: string
  /** Unix seconds — lets the UI show elapsed time so a slow step doesn't look frozen. */
  ts?: number
}

interface DeckRunPayload {
  query: string
  deck?: Record<string, unknown>
}

// Reads newline-delimited JSON from POST /api/deck/run as it arrives, so the
// caller can render each agent node's output as soon as it finishes instead
// of waiting for the whole (multi-LLM-call) graph run to complete.
export async function runDeck(
  payload: DeckRunPayload,
  onEvent: (event: DeckRunEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/deck/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`Run request failed (${res.status})`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const flushLine = (line: string) => {
    const trimmed = line.trim()
    if (trimmed) onEvent(JSON.parse(trimmed) as DeckRunEvent)
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let newlineIndex: number
    while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
      flushLine(buffer.slice(0, newlineIndex))
      buffer = buffer.slice(newlineIndex + 1)
    }
  }
  flushLine(buffer)
}

// Best-effort: flags the run server-side to stop before its next graph node
// (see deck_run.py's _cancel_flags) so it stops burning further LLM calls.
// It cannot interrupt an LLM call already in flight -- the caller's own
// AbortController on the runDeck() fetch is what actually frees the UI
// instantly, this is purely a "stop wasting server-side work" courtesy call.
export async function cancelRun(runId: string): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/deck/run/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
  } catch {
    // The run may have already finished, or the network call itself can race
    // with the fetch abort below -- either way there's nothing actionable here.
  }
}
