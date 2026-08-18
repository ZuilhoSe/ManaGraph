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
