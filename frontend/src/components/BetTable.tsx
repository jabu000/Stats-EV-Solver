import type { Mode, PricedBet } from '../lib/types'
import { confidenceColor, confidenceLabel, edgeColor, marketLabel, num, pct, signedPct, startTime } from '../lib/format'

interface Props {
  bets: PricedBet[]
  mode: Mode
  slip: PricedBet[]
  onSelect: (bet: PricedBet) => void
  onToggleSlip: (bet: PricedBet) => void
}

export function BetTable({ bets, mode, slip, onSelect, onToggleSlip }: Props) {
  const inSlip = new Set(slip.map((bet) => bet.id))

  if (bets.length === 0) {
    return (
      <div className="mx-auto max-w-[1600px] px-4 py-16 text-center text-slate-500">
        No bets match these filters.
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-4">
      <table className="w-full border-separate border-spacing-y-1 text-sm">
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
            <th className="px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">Player</th>
            <th className="px-3 py-2 font-medium">Bet</th>
            <th className="px-3 py-2 text-right font-medium">Line</th>
            <th className="px-3 py-2 text-right font-medium">Projected</th>
            <th className="px-3 py-2 text-right font-medium" title="Model probability this side hits">Win %</th>
            <th className="px-3 py-2 text-right font-medium" title="Probability needed to break even at your entry structure">Need</th>
            <th className="px-3 py-2 text-right font-medium">Edge</th>
            <th className="px-3 py-2 text-right font-medium" title="Expected return per dollar risked">EV</th>
            {mode === 'value' && (
              <th
                className="px-3 py-2 text-right font-medium text-emerald-400/80"
                title="Edge after shrinking toward break-even by confidence. This column is what the board is sorted by, so a well-measured smaller edge can outrank a shaky larger one."
              >
                Score ↓
              </th>
            )}
            <th className="px-3 py-2 font-medium">Confidence</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {bets.map((bet, index) => (
            <tr
              key={bet.id}
              onClick={() => onSelect(bet)}
              className="cursor-pointer bg-ink-900 transition-colors hover:bg-ink-850"
            >
              <td className="tabular rounded-l-lg px-3 py-2.5 text-slate-600">{index + 1}</td>

              <td className="px-3 py-2.5">
                <div className="font-medium text-white">{bet.player_name}</div>
                <div className="text-xs text-slate-500">
                  {bet.team}
                  {bet.opponent && ` vs ${bet.opponent}`}
                  {bet.starts_at && ` · ${startTime(bet.starts_at)}`}
                </div>
              </td>

              <td className="px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                      bet.side === 'higher'
                        ? 'bg-emerald-500/15 text-emerald-300'
                        : 'bg-sky-500/15 text-sky-300'
                    }`}
                  >
                    {bet.side === 'higher' ? 'HIGHER' : 'LOWER'}
                  </span>
                  <span className="text-slate-300">{marketLabel(bet.market)}</span>
                  {bet.payout_multiplier !== 1 && (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-xs font-semibold text-amber-300">
                      {bet.payout_multiplier}x
                    </span>
                  )}
                </div>
              </td>

              <td className="tabular px-3 py-2.5 text-right text-slate-300">{bet.stat_line}</td>
              <td className="tabular px-3 py-2.5 text-right text-white">{num(bet.projected_mean, 1)}</td>
              <td className="tabular px-3 py-2.5 text-right font-medium text-white">
                {pct(bet.calibrated_probability)}
              </td>
              <td className="tabular px-3 py-2.5 text-right text-slate-500">
                {pct(bet.break_even_probability)}
              </td>
              <td className={`tabular px-3 py-2.5 text-right font-semibold ${edgeColor(bet.edge)}`}>
                {signedPct(bet.edge)}
              </td>
              <td className={`tabular px-3 py-2.5 text-right ${edgeColor(bet.ev_per_dollar)}`}>
                {signedPct(bet.ev_per_dollar)}
              </td>

              {mode === 'value' && (
                <td className={`tabular px-3 py-2.5 text-right font-semibold ${edgeColor(bet.score)}`}>
                  {signedPct(bet.score)}
                </td>
              )}

              <td className="px-3 py-2.5">
                <span className={`rounded border px-1.5 py-0.5 text-xs ${confidenceColor(bet.confidence)}`}>
                  {confidenceLabel(bet.confidence)} {(bet.confidence * 100).toFixed(0)}
                </span>
                {bet.warnings.length > 0 && (
                  <span className="ml-1.5 text-amber-400" title={bet.warnings.join(' · ')}>⚠</span>
                )}
              </td>

              <td className="rounded-r-lg px-3 py-2.5 text-right">
                <button
                  onClick={(event) => { event.stopPropagation(); onToggleSlip(bet) }}
                  className={`rounded-md border px-2 py-1 text-xs transition-colors ${
                    inSlip.has(bet.id)
                      ? 'border-emerald-500/60 bg-emerald-500/15 text-emerald-300'
                      : 'border-ink-700 text-slate-400 hover:border-ink-600 hover:text-white'
                  }`}
                >
                  {inSlip.has(bet.id) ? 'In slip' : '+ Slip'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
