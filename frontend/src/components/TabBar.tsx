import type { League } from '../lib/types'

export type Tab = League | 'track' | 'settings'

const TABS: { id: Tab; label: string }[] = [
  { id: 'MLB', label: 'MLB' },
  { id: 'NFL', label: 'NFL' },
  { id: 'CFB', label: 'CFB' },
  { id: 'track', label: 'Track Record' },
  { id: 'settings', label: 'Settings' },
]

export function TabBar({ active, onChange }: { active: Tab; onChange: (tab: Tab) => void }) {
  return (
    <nav className="border-b border-ink-700 bg-ink-900" aria-label="Sections">
      <div className="mx-auto flex max-w-[1600px] items-center gap-1 px-4">
        <div className="mr-6 flex items-center gap-2 py-3">
          <span className="text-lg font-semibold tracking-tight text-white">EV Solver</span>
        </div>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            aria-current={active === tab.id ? 'page' : undefined}
            className={`relative px-4 py-3 text-sm font-medium transition-colors ${
              active === tab.id
                ? 'text-white'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
            {active === tab.id && (
              <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-emerald-400" />
            )}
          </button>
        ))}
      </div>
    </nav>
  )
}
