export type League = 'MLB' | 'NFL' | 'CFB'
export type Mode = 'value' | 'likely'
export type Side = 'higher' | 'lower'

export interface Factor {
  name: string
  detail: string
  impact: number
  direction: 'positive' | 'negative' | 'neutral'
}

export interface Distribution {
  mean: number; p10: number; p25: number; p50: number; p75: number; p90: number; std: number
}

export interface PricedBet {
  id: string
  league: League
  market: string
  underdog_line_id: string | null
  player_key: string
  player_name: string
  position: string | null
  team: string | null
  opponent: string | null
  game_label: string | null
  game_id: string | null
  starts_at: string | null
  stat_line: number
  side: Side
  payout_multiplier: number
  projected_mean: number
  distribution: Distribution
  model_probability: number
  calibrated_probability: number
  break_even_probability: number
  edge: number
  ev_per_dollar: number
  confidence: number
  score: number
  is_calibrated: boolean
  factors: Factor[]
  warnings: string[]
}

export interface BoardFilters {
  teams: string[]; games: string[]; positions: string[]; markets: string[]
}

export interface BoardResponse {
  league: League
  mode: Mode
  generated_at: string
  source: string
  bets: PricedBet[]
  filters: BoardFilters
  unmapped_count: number
  notes: string[]
}

export interface MarketOption { value: string; label: string }

export interface EntryOutcome {
  correct: number; probability: number; multiplier: number; contribution: number
}

export interface CorrelationWarning {
  leg_ids: string[]; kind: string; detail: string; severity: 'info' | 'warn' | 'block'
}

export interface EntryResponse {
  legs: number
  entry_type: string
  stake: number
  payout_table: EntryOutcome[]
  expected_return: number
  expected_profit: number
  ev_percent: number
  win_probability: number
  kelly_stake: number
  kelly_full: number
  correlation_warnings: CorrelationWarning[]
  notes: string[]
}

export interface ProviderStatus {
  provider: string; label: string; ok: boolean; mode: string
  status: string; detail: string; duration_ms: number
  requires_key: boolean; key_present: boolean
}

export interface AppSettings {
  underdog_token_set: boolean
  cfbd_api_key_set: boolean
  reference_entry_type: string
  reference_entry_legs: number
  standard_payouts: Record<string, number>
  insured_payouts: Record<string, Record<string, number>>
  bankroll: number
  kelly_fraction: number
  default_mode: Mode
  min_edge: number
  min_confidence: number
  hide_negative_ev: boolean
  data_mode: string
}

export interface CalibrationBucket {
  lower: number; upper: number; predicted: number; actual: number; count: number
}

export interface MarketRecord {
  league: League; market: string; picks: number; wins: number
  hit_rate: number; expected_hit_rate: number; roi: number; brier: number
}

export interface TrackRecord {
  total_picks: number
  graded_picks: number
  pending_picks: number
  wins: number
  hit_rate: number
  expected_hit_rate: number
  roi: number
  brier_score: number
  avg_clv: number | null
  calibration: CalibrationBucket[]
  by_market: MarketRecord[]
  roi_series: { index: number; date: string | null; units: number }[]
  recent: Record<string, unknown>[]
}
