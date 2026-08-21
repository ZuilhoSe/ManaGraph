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
