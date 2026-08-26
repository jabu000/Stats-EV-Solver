import { useEffect, useState } from 'react'
import { priceEntry } from '../lib/api'
import type { EntryResponse, PricedBet } from '../lib/types'
import { edgeColor, marketLabel, pct } from '../lib/format'

interface Props {
  slip: PricedBet[]
  onRemove: (bet: PricedBet) => void
  onClear: () => void
}

/**
 * Slip builder. Prices the whole entry rather than the legs in isolation, because at
 * Underdog the payout depends on the entry size and type -- three 58% legs are a very
 * different proposition in a standard 3-pick than in an insured 5-pick.
 */
export function EntryBuilder({ slip, onRemove, onClear }: Props) {
  const [entryType, setEntryType] = useState('standard')
  const [stake, setStake] = useState(10)
  const [result, setResult] = useState<EntryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    if (slip.length === 0) { setResult(null); return }
    let cancelled = false
    priceEntry(slip, entryType, stake)
      .then((response) => { if (!cancelled) { setResult(response); setError(null) } })
      .catch((err) => { if (!cancelled) setError(String(err.message ?? err)) })
    return () => { cancelled = true }
  }, [slip, entryType, stake])

  if (slip.length === 0) return null

  return (
    <div className="fixed bottom-0 right-0 z-30 w-full max-w-md border-l border-t border-ink-700 bg-ink-900 shadow-2xl">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <span className="font-semibold text-white">
          Entry slip <span className="text-slate-500">({slip.length})</span>
        </span>
        <span className="flex items-center gap-3">
          {result && (
            <span className={`tabular text-sm font-semibold ${edgeColor(result.ev_percent / 100)}`}>
              {result.ev_percent >= 0 ? '+' : ''}{result.ev_percent.toFixed(1)}% EV
            </span>
          )}
          <span className="text-slate-500">{open ? '▾' : '▴'}</span>
        </span>
      </button>

      {open && (
        <div className="max-h-[70vh] overflow-y-auto border-t border-ink-800 px-4 py-3">
          <ul className="space-y-1.5">
            {slip.map((bet) => (
              <li key={bet.id} className="flex items-center justify-between gap-2 rounded-lg bg-ink-850 px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm text-white">{bet.player_name}</div>
                  <div className="text-xs text-slate-500">
                    {bet.side === 'higher' ? 'Higher' : 'Lower'} {bet.stat_line} {marketLabel(bet.market)}
                    {' · '}{pct(bet.calibrated_probability, 0)}
                  </div>
                </div>
                <button onClick={() => onRemove(bet)} className="shrink-0 text-slate-600 hover:text-rose-400" aria-label="Remove">✕</button>
              </li>
            ))}
          </ul>

          <div className="mt-3 flex items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-[11px] uppercase tracking-wide text-slate-500">Entry type</span>
              <select
                value={entryType}
                onChange={(event) => setEntryType(event.target.value)}
                className="rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-200"
              >
                <option value="standard">Standard</option>
                <option value="insured">Insured</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[11px] uppercase tracking-wide text-slate-500">Stake</span>
              <input
                type="number" min={1} value={stake}
                onChange={(event) => setStake(Number(event.target.value) || 0)}
                className="w-24 rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-200"
              />
            </label>
            <button onClick={onClear} className="ml-auto text-xs text-slate-500 hover:text-rose-400">Clear all</button>
          </div>

          {error && <p className="mt-3 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300">{error}</p>}

          {result && (
            <div className="mt-3 space-y-3">
              <div className="grid grid-cols-3 gap-2">
                <Metric label="EV" value={`${result.ev_percent >= 0 ? '+' : ''}${result.ev_percent.toFixed(1)}%`} className={edgeColor(result.ev_percent / 100)} />
                <Metric label="Win chance" value={pct(result.win_probability)} />
                <Metric label={`Kelly (${(result.kelly_full * 100).toFixed(1)}% full)`} value={`$${result.kelly_stake.toFixed(2)}`} />
              </div>

              <div>
                <h4 className="mb-1.5 text-[11px] uppercase tracking-wide text-slate-500">Payout branches</h4>
                <table className="w-full text-xs">
                  <tbody>
                    {result.payout_table.map((outcome) => (
                      <tr key={outcome.correct} className="border-b border-ink-850 last:border-0">
                        <td className="py-1 text-slate-400">{outcome.correct}/{result.legs} correct</td>
                        <td className="tabular py-1 text-right text-slate-300">{pct(outcome.probability)}</td>
                        <td className="tabular py-1 text-right text-slate-300">{outcome.multiplier.toFixed(2)}x</td>
                        <td className="tabular py-1 text-right text-white">${outcome.contribution.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {result.correlation_warnings.map((warning, index) => (
                <p
                  key={index}
                  className={`rounded-lg px-3 py-2 text-xs ${
                    warning.severity === 'warn'
                      ? 'border border-amber-500/30 bg-amber-500/10 text-amber-200'
                      : 'border border-ink-700 bg-ink-850 text-slate-400'
                  }`}
                >
                  {warning.detail}
                </p>
              ))}

              {result.notes.map((note) => (
                <p key={note} className="text-xs text-slate-500">{note}</p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Metric({ label, value, className = '' }: { label: string; value: string; className?: string }) {
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-850 px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`tabular mt-0.5 text-sm font-semibold text-white ${className}`}>{value}</div>
    </div>
  )
}
