import { useEffect, useState } from 'react'
import { fetchTrackRecord, gradeNow } from '../lib/api'
import type { League, TrackRecord } from '../lib/types'
import { edgeColor, marketLabel, pct, signedPct } from '../lib/format'

export function TrackRecordPage() {
  const [league, setLeague] = useState<League | ''>('')
  const [record, setRecord] = useState<TrackRecord | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [grading, setGrading] = useState(false)
  const [gradeNote, setGradeNote] = useState<string[] | null>(null)

  const load = () =>
    fetchTrackRecord(league || undefined)
      .then((response) => { setRecord(response); setError(null) })
      .catch((err) => setError(String(err.message ?? err)))

  useEffect(() => { load() }, [league])

  const runGrading = () => {
    setGrading(true)
    setGradeNote(null)
    gradeNow(league || undefined)
      .then((report) => {
        const lines = report.reports.length === 0
          ? ['Nothing is waiting on results.']
          : [`Settled ${report.graded} picks for ${report.date}.`]
        for (const entry of report.reports) {
          if (entry.still_pending > 0) {
            lines.push(`${entry.league}: ${entry.still_pending} still pending.`)
          }
          // Surface every reason a pick could not be graded — a grading run that
          // quietly settles a biased subset is worse than one that fails loudly.
          for (const problem of entry.problems) lines.push(`${entry.league}: ${problem}`)
        }
        setGradeNote(lines)
        return load()
      })
      .catch((err) => setError(String(err.message ?? err)))
      .finally(() => setGrading(false))
  }

  if (error) {
    return <Shell><p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{error}</p></Shell>
  }
  if (!record) return <Shell><p className="text-slate-500">Loading…</p></Shell>

  return (
    <Shell>
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Track record</h1>
          <p className="mt-0.5 text-sm text-slate-500">
            Every projection the platform published, graded against what actually happened.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {record && record.pending_picks > 0 && (
            <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-xs text-amber-200">
              {record.pending_picks} awaiting results
            </span>
          )}
          <button
            onClick={runGrading}
            disabled={grading}
            title="Fetch real results and settle any recorded picks they cover."
            className="rounded-md bg-emerald-500 px-3 py-1.5 text-sm font-medium text-ink-950 hover:bg-emerald-400 disabled:opacity-50"
          >
            {grading ? 'Grading…' : 'Grade now'}
          </button>
          <select
            value={league}
            onChange={(event) => setLeague(event.target.value as League | '')}
            className="rounded-md border border-ink-700 bg-ink-850 px-3 py-1.5 text-sm text-slate-200"
          >
            <option value="">All leagues</option>
            <option value="MLB">MLB</option>
            <option value="NFL">NFL</option>
            <option value="CFB">CFB</option>
          </select>
        </div>
      </div>

      {gradeNote && (
        <ul className="mb-4 space-y-1 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2">
          {gradeNote.map((line, index) => (
            <li key={index} className="text-xs text-slate-400">{line}</li>
          ))}
        </ul>
      )}

      {record.graded_picks === 0 ? (
        <div className="rounded-xl border border-ink-700 bg-ink-900 px-5 py-8 text-center">
          <p className="text-slate-300">
            {record.total_picks.toLocaleString()} picks recorded, none graded yet.
          </p>
          <p className="mx-auto mt-2 max-w-xl text-sm text-slate-500">
            Record a slate from a league tab, then hit <strong>Grade now</strong> once the
            games finish — or let the scheduled job do both. Until picks are graded, model
            probabilities stay <strong>uncalibrated</strong> and the platform will not
            pretend to know how accurate it is.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Card label="Graded picks" value={record.graded_picks.toLocaleString()} sub={`${record.pending_picks} pending`} />
            <Card label="Hit rate" value={pct(record.hit_rate)} sub={`model said ${pct(record.expected_hit_rate)}`} />
            <Card label="ROI" value={signedPct(record.roi)} className={edgeColor(record.roi)} sub="per unit staked" />
            <Card label="Brier score" value={record.brier_score.toFixed(4)} sub="lower is better" />
            <Card label="Avg CLV" value={record.avg_clv === null ? '—' : record.avg_clv.toFixed(2)} sub="closing line value" />
          </div>

          <section className="mt-6 grid gap-4 lg:grid-cols-2">
            <Panel title="Calibration" hint="Where the bars match the dashed line, the stated probabilities are honest.">
              <CalibrationChart record={record} />
            </Panel>
            <Panel title="Cumulative units" hint="Units won or lost over graded picks, in order.">
              <RoiChart record={record} />
            </Panel>
          </section>

          <section className="mt-6">
            <h2 className="mb-2 text-sm font-medium text-slate-300">By market</h2>
            <div className="overflow-x-auto rounded-xl border border-ink-800">
              <table className="w-full text-sm">
                <thead className="bg-ink-900 text-[11px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">League</th>
                    <th className="px-3 py-2 text-left font-medium">Market</th>
                    <th className="px-3 py-2 text-right font-medium">Picks</th>
                    <th className="px-3 py-2 text-right font-medium">Hit rate</th>
                    <th className="px-3 py-2 text-right font-medium">Model said</th>
                    <th className="px-3 py-2 text-right font-medium">ROI</th>
                    <th className="px-3 py-2 text-right font-medium">Brier</th>
                  </tr>
                </thead>
                <tbody>
                  {record.by_market.map((row) => (
                    <tr key={`${row.league}-${row.market}`} className="border-t border-ink-850">
                      <td className="px-3 py-2 text-slate-400">{row.league}</td>
                      <td className="px-3 py-2 text-slate-200">{marketLabel(row.market)}</td>
                      <td className="tabular px-3 py-2 text-right text-slate-300">{row.picks}</td>
                      <td className="tabular px-3 py-2 text-right text-white">{pct(row.hit_rate)}</td>
                      <td className="tabular px-3 py-2 text-right text-slate-500">{pct(row.expected_hit_rate)}</td>
                      <td className={`tabular px-3 py-2 text-right ${edgeColor(row.roi)}`}>{signedPct(row.roi)}</td>
                      <td className="tabular px-3 py-2 text-right text-slate-400">{row.brier.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-[1600px] px-4 py-6">{children}</div>
}

function Card({ label, value, sub, className = '' }: { label: string; value: string; sub?: string; className?: string }) {
  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900 px-4 py-3">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`tabular mt-1 text-2xl font-semibold text-white ${className}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

function Panel({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900 px-4 py-3">
      <h2 className="text-sm font-medium text-slate-300">{title}</h2>
      <p className="mt-0.5 text-xs text-slate-500">{hint}</p>
      <div className="mt-3">{children}</div>
    </div>
  )
}

/** Predicted vs actual by probability bucket, drawn as inline SVG (no chart library). */
function CalibrationChart({ record }: { record: TrackRecord }) {
  const width = 380, height = 200, pad = 28
  const scale = (value: number) => ({
    x: pad + value * (width - pad * 2),
    y: height - pad - value * (height - pad * 2),
  })
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Calibration chart">
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="#33436b" />
      <line x1={pad} y1={pad} x2={pad} y2={height - pad} stroke="#33436b" />
      <line
        x1={scale(0).x} y1={scale(0).y} x2={scale(1).x} y2={scale(1).y}
        stroke="#64748b" strokeDasharray="4 4"
      />
      {record.calibration.map((bucket) => {
        const point = scale(bucket.predicted)
        const actual = scale(bucket.actual)
        return (
          <g key={`${bucket.lower}-${bucket.upper}`}>
            <line x1={point.x} y1={point.y} x2={point.x} y2={actual.y} stroke="#33436b" />
            <circle cx={point.x} cy={actual.y} r={Math.min(8, 3 + Math.sqrt(bucket.count) / 3)} fill="#34d399" fillOpacity={0.8} />
          </g>
        )
      })}
      <text x={width / 2} y={height - 6} textAnchor="middle" fill="#64748b" fontSize="10">predicted probability</text>
      <text x={10} y={height / 2} textAnchor="middle" fill="#64748b" fontSize="10" transform={`rotate(-90 10 ${height / 2})`}>actual</text>
    </svg>
  )
}

function RoiChart({ record }: { record: TrackRecord }) {
  const width = 380, height = 200, pad = 28
  const series = record.roi_series
  if (series.length < 2) return <p className="text-xs text-slate-500">Not enough graded picks yet.</p>

  const units = series.map((point) => point.units)
  const min = Math.min(0, ...units), max = Math.max(0, ...units)
  const span = Math.max(max - min, 1e-6)
  const x = (index: number) => pad + (index / (series.length - 1)) * (width - pad * 2)
  const y = (value: number) => height - pad - ((value - min) / span) * (height - pad * 2)
  const path = series.map((point, index) => `${index === 0 ? 'M' : 'L'}${x(index)},${y(point.units)}`).join(' ')
  const last = units[units.length - 1]

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Cumulative units chart">
      <line x1={pad} y1={y(0)} x2={width - pad} y2={y(0)} stroke="#33436b" strokeDasharray="4 4" />
      <path d={path} fill="none" stroke={last >= 0 ? '#34d399' : '#f87171'} strokeWidth={2} />
      <text x={width - pad} y={y(last) - 6} textAnchor="end" fill={last >= 0 ? '#34d399' : '#f87171'} fontSize="11">
        {last >= 0 ? '+' : ''}{last.toFixed(1)}u
      </text>
    </svg>
  )
}
