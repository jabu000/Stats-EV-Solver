import type { PricedBet } from '../lib/types'
import { edgeColor, marketLabel, num, pct, signedPct } from '../lib/format'

/**
 * The factor breakdown behind a projection.
 *
 * Without this the platform is a black box telling someone to risk money. Every input
 * that moved the number is listed, in the market's own units where it has one.
 */
export function WhyDrawer({ bet, onClose }: { bet: PricedBet | null; onClose: () => void }) {
  if (!bet) return null

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/60" onClick={onClose} aria-hidden />
      <aside className="fixed right-0 top-0 z-50 flex h-full w-full max-w-lg flex-col border-l border-ink-700 bg-ink-900 shadow-2xl">
        <header className="flex items-start justify-between border-b border-ink-800 px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold text-white">{bet.player_name}</h2>
            <p className="text-sm text-slate-400">
              {bet.side === 'higher' ? 'Higher' : 'Lower'} {bet.stat_line} {marketLabel(bet.market)}
              {bet.team && ` · ${bet.team}`}
              {bet.opponent && ` vs ${bet.opponent}`}
            </p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-slate-500 hover:text-white" aria-label="Close">✕</button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Model probability" value={pct(bet.calibrated_probability)} />
            <Stat label="Break-even needed" value={pct(bet.break_even_probability)} />
            <Stat label="Edge" value={signedPct(bet.edge)} className={edgeColor(bet.edge)} />
            <Stat label="EV per $1" value={signedPct(bet.ev_per_dollar)} className={edgeColor(bet.ev_per_dollar)} />
          </div>

          {!bet.is_calibrated && (
            <p className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              This probability is <strong>uncalibrated</strong> — there is not yet enough graded
              history for this market to correct the model against real results.
            </p>
          )}

          {bet.warnings.length > 0 && (
            <ul className="mt-3 space-y-1.5">
              {bet.warnings.map((warning) => (
                <li key={warning} className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                  {warning}
                </li>
              ))}
            </ul>
          )}

          <section className="mt-5">
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Projected distribution
            </h3>
            <DistributionBar bet={bet} />
          </section>

          <section className="mt-5">
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wide text-slate-500">
              What moved this projection
            </h3>
            <ul className="space-y-1.5">
              {bet.factors.map((factor, index) => (
                <li key={`${factor.name}-${index}`} className="rounded-lg border border-ink-800 bg-ink-850 px-3 py-2">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-sm font-medium text-slate-200">{factor.name}</span>
                    <span
                      className={`text-xs ${
                        factor.direction === 'positive' ? 'text-edge-pos'
                          : factor.direction === 'negative' ? 'text-edge-neg' : 'text-slate-500'
                      }`}
                    >
                      {factor.direction === 'positive' ? '▲' : factor.direction === 'negative' ? '▼' : '–'}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs leading-relaxed text-slate-400">{factor.detail}</p>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </aside>
    </>
  )
}

function Stat({ label, value, className = '' }: { label: string; value: string; className?: string }) {
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-850 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`tabular mt-0.5 text-lg font-semibold text-white ${className}`}>{value}</div>
    </div>
  )
}

/** Shows where the posted line sits inside the projected p10–p90 range. */
function DistributionBar({ bet }: { bet: PricedBet }) {
  const { p10, p90 } = bet.distribution
  const span = Math.max(p90 - p10, 1e-6)
  const clamp = (value: number) => Math.max(0, Math.min(100, ((value - p10) / span) * 100))

  return (
    <div className="rounded-lg border border-ink-800 bg-ink-850 px-3 py-3">
      <div className="relative h-2 rounded-full bg-ink-700">
        <div
          className="absolute inset-y-0 rounded-full bg-emerald-500/40"
          style={{ left: `${clamp(bet.distribution.p25)}%`, right: `${100 - clamp(bet.distribution.p75)}%` }}
        />
        <div className="absolute -top-1 h-4 w-0.5 bg-white" style={{ left: `${clamp(bet.distribution.p50)}%` }} title="Median" />
        <div className="absolute -top-1.5 h-5 w-0.5 bg-amber-400" style={{ left: `${clamp(bet.stat_line)}%` }} title="Posted line" />
      </div>
      <div className="tabular mt-2 flex justify-between text-[11px] text-slate-500">
        <span>p10 {num(p10)}</span>
        <span className="text-white">median {num(bet.distribution.p50)}</span>
        <span>p90 {num(p90)}</span>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        <span className="text-amber-400">▌</span> posted line {bet.stat_line} ·
        mean {num(bet.distribution.mean)} · sd {num(bet.distribution.std)}
      </p>
    </div>
  )
}
