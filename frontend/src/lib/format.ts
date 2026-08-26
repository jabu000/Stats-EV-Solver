/** Display helpers. Kept in one place so the same number always reads the same way. */

export const pct = (value: number, digits = 1) => `${(value * 100).toFixed(digits)}%`

export const signedPct = (value: number, digits = 1) =>
  `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`

export const num = (value: number, digits = 1) => value.toFixed(digits)

/** Green for a positive edge, red for negative, grey when it is a rounding error. */
export const edgeColor = (edge: number) =>
  edge > 0.005 ? 'text-edge-pos' : edge < -0.005 ? 'text-edge-neg' : 'text-slate-400'

export const confidenceLabel = (confidence: number) =>
  confidence >= 0.8 ? 'High' : confidence >= 0.55 ? 'Medium' : 'Low'

export const confidenceColor = (confidence: number) =>
  confidence >= 0.8
    ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
    : confidence >= 0.55
      ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
      : 'bg-rose-500/15 text-rose-300 border-rose-500/30'

export const marketLabels: Record<string, string> = {
  strikeouts: 'Strikeouts',
  hits_1_plus: '1+ Hit',
  receiving_yards: 'Receiving Yards',
  rushing_yards: 'Rushing Yards',
  passing_yards: 'Passing Yards',
  anytime_td: 'Anytime TD',
  receptions: 'Receptions',
}

export const marketLabel = (market: string) => marketLabels[market] ?? market

export const startTime = (iso: string | null) => {
  if (!iso) return ''
  const date = new Date(iso)
  return date.toLocaleString(undefined, {
    weekday: 'short', hour: 'numeric', minute: '2-digit',
  })
}
