import { useEffect, useState } from 'react'
import { fetchSettings, saveSettings, testConnections } from '../lib/api'
import type { AppSettings, ProviderStatus } from '../lib/types'
import { pct } from '../lib/format'

export function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [statuses, setStatuses] = useState<ProviderStatus[] | null>(null)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [tokens, setTokens] = useState({ underdog_token: '', cfbd_api_key: '' })

  useEffect(() => { fetchSettings().then(setSettings).catch(() => setMessage('Could not load settings.')) }, [])

  const patch = async (updates: Record<string, unknown>) => {
    try {
      setSettings(await saveSettings(updates))
      setMessage('Saved.')
      setTimeout(() => setMessage(null), 2000)
    } catch (error) {
      setMessage(String((error as Error).message))
    }
  }

  const runTest = async () => {
    setTesting(true)
    try { setStatuses(await testConnections()) }
    catch (error) { setMessage(String((error as Error).message)) }
    finally { setTesting(false) }
  }

  if (!settings) return <Shell><p className="text-slate-500">Loading…</p></Shell>

  return (
    <Shell>
      <h1 className="text-xl font-semibold text-white">Settings</h1>
      <p className="mt-0.5 text-sm text-slate-500">
        Running in <strong className="text-slate-300">{settings.data_mode}</strong> mode.
      </p>
      {message && <p className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">{message}</p>}

      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <Section
          title="Credentials"
          hint="Underdog's endpoint is usually readable without a token. If it starts refusing, paste a bearer token here — it takes effect immediately, no restart."
        >
          <Secret
            label="Underdog bearer token"
            isSet={settings.underdog_token_set}
            value={tokens.underdog_token}
            onChange={(value) => setTokens({ ...tokens, underdog_token: value })}
            onSave={() => patch({ underdog_token: tokens.underdog_token }).then(() => setTokens({ ...tokens, underdog_token: '' }))}
          />
          <Secret
            label="CollegeFootballData API key"
            isSet={settings.cfbd_api_key_set}
            hint="Free at collegefootballdata.com. Required for the CFB tab."
            value={tokens.cfbd_api_key}
            onChange={(value) => setTokens({ ...tokens, cfbd_api_key: value })}
            onSave={() => patch({ cfbd_api_key: tokens.cfbd_api_key }).then(() => setTokens({ ...tokens, cfbd_api_key: '' }))}
          />
        </Section>

        <Section
          title="How EV is measured"
          hint="A Pick'em leg is not a coin flip. A standard 3-pick pays 6x and needs all three to land, so each leg must hit about 55% just to break even — not 50%. Every edge on the board is measured against the entry shape you pick here."
        >
          <div className="flex gap-3">
            <Labeled label="Reference entry">
              <select
                value={settings.reference_entry_type}
                onChange={(event) => patch({ reference_entry_type: event.target.value })}
                className="w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-200"
              >
                <option value="standard">Standard</option>
                <option value="insured">Insured</option>
              </select>
            </Labeled>
            <Labeled label="Legs">
              <select
                value={settings.reference_entry_legs}
                onChange={(event) => patch({ reference_entry_legs: Number(event.target.value) })}
                className="w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-200"
              >
                {[2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}-pick</option>)}
              </select>
            </Labeled>
          </div>

          <div className="mt-3">
            <div className="mb-1.5 text-[11px] uppercase tracking-wide text-slate-500">Standard payout multipliers</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(settings.standard_payouts).map(([legs, multiplier]) => (
                <label key={legs} className="flex items-center gap-1.5 rounded-md border border-ink-700 bg-ink-850 px-2 py-1">
                  <span className="text-xs text-slate-500">{legs}-pick</span>
                  <input
                    type="number" step={0.25} defaultValue={multiplier}
                    onBlur={(event) =>
                      patch({ standard_payouts: { ...settings.standard_payouts, [legs]: Number(event.target.value) } })
                    }
                    className="tabular w-16 bg-transparent text-sm text-white focus:outline-none"
                  />
                  <span className="text-xs text-slate-500">x</span>
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs text-slate-500">
              Update these whenever Underdog changes its structure — the whole board reprices off them.
            </p>
          </div>
        </Section>

        <Section title="Bankroll" hint="Used to size the Kelly stake shown on the entry slip.">
          <div className="flex gap-3">
            <Labeled label="Bankroll ($)">
              <input
                type="number" defaultValue={settings.bankroll}
                onBlur={(event) => patch({ bankroll: Number(event.target.value) })}
                className="w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-200"
              />
            </Labeled>
            <Labeled label={`Kelly fraction (${pct(settings.kelly_fraction, 0)})`}>
              <input
                type="range" min={0.05} max={1} step={0.05} defaultValue={settings.kelly_fraction}
                onMouseUp={(event) => patch({ kelly_fraction: Number((event.target as HTMLInputElement).value) })}
                className="w-full accent-emerald-500"
              />
            </Labeled>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Quarter-Kelly is the usual choice: full Kelly is theoretically optimal but assumes your
            probabilities are exactly right, which no sports model's are.
          </p>
        </Section>

        <Section title="Board defaults" hint="Applied before the board is built, so filtered-out bets never reach the table.">
          <Labeled label={`Minimum edge (${pct(settings.min_edge, 1)})`}>
            <input
              type="range" min={0} max={0.15} step={0.005} defaultValue={settings.min_edge}
              onMouseUp={(event) => patch({ min_edge: Number((event.target as HTMLInputElement).value) })}
              className="w-full accent-emerald-500"
            />
          </Labeled>
          <Labeled label={`Minimum confidence (${pct(settings.min_confidence, 0)})`}>
            <input
              type="range" min={0} max={1} step={0.05} defaultValue={settings.min_confidence}
              onMouseUp={(event) => patch({ min_confidence: Number((event.target as HTMLInputElement).value) })}
              className="w-full accent-emerald-500"
            />
          </Labeled>
          <label className="mt-2 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox" defaultChecked={settings.hide_negative_ev}
              onChange={(event) => patch({ hide_negative_ev: event.target.checked })}
              className="accent-emerald-500"
            />
            Hide negative-EV bets entirely
          </label>
        </Section>
      </div>

      <Section
        title="Data sources"
        hint="Check what is actually answering. This is the fastest way to tell whether a thin board is a quiet slate or a broken feed."
        className="mt-5"
      >
        <button
          onClick={runTest}
          disabled={testing}
          className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-ink-950 hover:bg-emerald-400 disabled:opacity-50"
        >
          {testing ? 'Testing…' : 'Test connections'}
        </button>

        {statuses && (
          <ul className="mt-3 space-y-1.5">
            {statuses.map((status) => (
              <li key={status.provider} className="flex items-center gap-3 rounded-lg border border-ink-800 bg-ink-850 px-3 py-2">
                <span className={`h-2 w-2 shrink-0 rounded-full ${status.ok ? 'bg-emerald-400' : 'bg-rose-400'}`} />
                <span className="w-56 shrink-0 text-sm text-slate-200">{status.label}</span>
                <span className="text-xs text-slate-500">{status.detail || status.status}</span>
                {status.requires_key && !status.key_present && (
                  <span className="ml-auto rounded bg-amber-500/15 px-1.5 py-0.5 text-xs text-amber-300">key needed</span>
                )}
                <span className="tabular ml-auto text-xs text-slate-600">{status.duration_ms}ms</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-[1600px] px-4 py-6 pb-40">{children}</div>
}

function Section({
  title, hint, children, className = '',
}: { title: string; hint: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-ink-800 bg-ink-900 px-4 py-4 ${className}`}>
      <h2 className="text-sm font-medium text-slate-200">{title}</h2>
      <p className="mt-1 text-xs leading-relaxed text-slate-500">{hint}</p>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex w-full flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  )
}

function Secret({
  label, hint, isSet, value, onChange, onSave,
}: {
  label: string; hint?: string; isSet: boolean
  value: string; onChange: (value: string) => void; onSave: () => void
}) {
  return (
    <div className="mb-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">{label}</span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${isSet ? 'bg-emerald-500/15 text-emerald-300' : 'bg-ink-800 text-slate-500'}`}>
          {isSet ? 'set' : 'not set'}
        </span>
      </div>
      <div className="mt-1 flex gap-2">
        <input
          type="password" value={value} placeholder={isSet ? '•••••••• (leave blank to keep)' : 'Paste here'}
          onChange={(event) => onChange(event.target.value)}
          className="flex-1 rounded-md border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-sm text-slate-200 placeholder:text-slate-600"
        />
        <button
          onClick={onSave} disabled={!value}
          className="rounded-md border border-ink-700 px-3 py-1.5 text-sm text-slate-300 hover:text-white disabled:opacity-40"
        >
          Save
        </button>
      </div>
      {hint && <p className="mt-1 text-xs text-slate-600">{hint}</p>}
    </div>
  )
}
