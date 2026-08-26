import type {
  AppSettings, BoardResponse, EntryResponse, League, MarketOption,
  Mode, PricedBet, ProviderStatus, TrackRecord,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const body = await response.text()
    let detail = body
    try { detail = JSON.parse(body).detail ?? body } catch { /* plain text body */ }
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

export interface BoardQuery {
  mode: Mode
  market?: string
  team?: string
  game?: string
  position?: string
  search?: string
  minEdge?: number
  minProbability?: number
  minConfidence?: number
}

export function fetchBoard(league: League, query: BoardQuery): Promise<BoardResponse> {
  const params = new URLSearchParams({ mode: query.mode })
  if (query.market) params.set('market', query.market)
  if (query.team) params.set('team', query.team)
  if (query.game) params.set('game', query.game)
  if (query.position) params.set('position', query.position)
  if (query.search) params.set('search', query.search)
  if (query.minEdge) params.set('min_edge', String(query.minEdge))
  if (query.minProbability) params.set('min_probability', String(query.minProbability))
  if (query.minConfidence) params.set('min_confidence', String(query.minConfidence))
  return request<BoardResponse>(`/api/board/${league}?${params}`)
}

export const fetchMarkets = (league: League) =>
  request<MarketOption[]>(`/api/markets/${league}`)

export const importSlate = (league: League, text: string, mode: Mode) =>
  request<BoardResponse>(`/api/board/${league}/import?mode=${mode}`, {
    method: 'POST', body: JSON.stringify({ text }),
  })

export const priceEntry = (
  legs: PricedBet[], entryType: string, stake: number,
) =>
  request<EntryResponse>('/api/entry/ev', {
    method: 'POST',
    body: JSON.stringify({
      legs: legs.map((bet) => ({
        bet_id: bet.id,
        player_name: bet.player_name,
        market: bet.market,
        side: bet.side,
        stat_line: bet.stat_line,
        probability: bet.calibrated_probability,
        payout_multiplier: bet.payout_multiplier,
        game_id: bet.game_id,
        team: bet.team,
      })),
      entry_type: entryType,
      stake,
    }),
  })

export const fetchSettings = () => request<AppSettings>('/api/settings')

export const saveSettings = (updates: Record<string, unknown>) =>
  request<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(updates) })

export const testConnections = () =>
  request<ProviderStatus[]>('/api/settings/test-connections', { method: 'POST' })

export const fetchTrackRecord = (league?: League) =>
  request<TrackRecord>(`/api/track-record${league ? `?league=${league}` : ''}`)
