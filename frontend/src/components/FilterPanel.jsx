import { useMemo, useState } from 'react'

const emptyRow = () => ({ id: crypto.randomUUID(), value: '' })

const SOURCES = [
  {
    id: 'online',
    label: 'Online',
    hint: 'Remotive, RemoteOK, Arbeitnow, Jobicy',
  },
  {
    id: 'urls',
    label: 'Boards',
    hint: 'Careers pages you paste below',
  },
  {
    id: 'both',
    label: 'Both',
    hint: 'Online first, then your boards',
  },
]

export default function FilterPanel({ onStart, disabled, scrape }) {
  const [mode, setMode] = useState('online')
  const [urls, setUrls] = useState([emptyRow()])
  const [keywords, setKeywords] = useState('annotator, data labeling, AI trainer, RLHF')
  const [minJobs, setMinJobs] = useState(10)
  const [localError, setLocalError] = useState('')

  const needsUrls = mode === 'urls' || mode === 'both'
  const needsKeywords = mode === 'online' || mode === 'both'
  const cleanUrls = useMemo(
    () => urls.map((u) => u.value.trim()).filter(Boolean),
    [urls],
  )

  const startLabel = useMemo(() => {
    if (disabled) {
      if (mode === 'online') return 'Searching…'
      if (mode === 'urls') return 'Scraping…'
      return 'Collecting…'
    }
    if (mode === 'online') return 'Search online'
    if (mode === 'urls') return 'Scrape boards'
    return 'Search & scrape'
  }, [disabled, mode])

  function updateUrl(id, value) {
    setUrls((rows) => rows.map((r) => (r.id === id ? { ...r, value } : r)))
  }

  function addUrl() {
    setUrls((rows) => [...rows, emptyRow()])
  }

  function removeUrl(id) {
    setUrls((rows) => (rows.length <= 1 ? rows : rows.filter((r) => r.id !== id)))
  }

  function handleSubmit(e) {
    e.preventDefault()
    setLocalError('')

    if (needsKeywords && !keywords.trim()) {
      setLocalError('Add at least one keyword for online search.')
      return
    }
    if (needsUrls && !cleanUrls.length) {
      setLocalError('Add at least one board URL, or switch source to Online.')
      return
    }

    onStart({
      urls: cleanUrls,
      keywords: keywords.trim(),
      min_jobs: Number(minJobs) || 1,
      mode,
    })
  }

  const progressPct = scrape?.target
    ? Math.min(100, (100 * (scrape.collected || 0)) / scrape.target)
    : 0

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      <div>
        <h2 className="text-base font-semibold">Find jobs</h2>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Board scrapes work without keywords. Online search needs keywords. Contract roles only.
        </p>
      </div>

      <div className="space-y-1.5">
        <label className="block text-sm font-medium" htmlFor="keywords">
          Keywords{' '}
          {needsKeywords ? (
            <span className="text-[var(--danger)]">*</span>
          ) : (
            <span className="font-normal text-[var(--muted)]">(optional)</span>
          )}
        </label>
        <textarea
          id="keywords"
          value={keywords}
          onChange={(e) => setKeywords(e.target.value)}
          rows={3}
          className="w-full rounded-lg border border-[var(--line)] bg-white px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
          disabled={disabled}
          placeholder="annotator, RLHF, data labeling"
          required={needsKeywords}
        />
        <p className="text-xs text-[var(--muted)]">
          {mode === 'urls'
            ? 'Leave blank to pull all contract roles from your boards, or add keywords to narrow results.'
            : 'Comma-separated. Used to find and filter listings (title + description).'}
        </p>
      </div>

      <div className="space-y-2">
        <span className="block text-sm font-medium">Source</span>
        <div
          className="grid grid-cols-3 gap-1 rounded-xl border border-[var(--line)] bg-[var(--surface)] p-1"
          role="radiogroup"
          aria-label="Job source"
        >
          {SOURCES.map((s) => {
            const active = mode === s.id
            return (
              <button
                key={s.id}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={disabled}
                onClick={() => {
                  setMode(s.id)
                  setLocalError('')
                }}
                className={[
                  'rounded-lg px-2 py-2 text-sm font-medium transition-colors',
                  active
                    ? 'bg-white text-[var(--ink)] shadow-sm'
                    : 'text-[var(--muted)] hover:text-[var(--ink)]',
                  disabled ? 'opacity-60' : '',
                ].join(' ')}
              >
                {s.label}
              </button>
            )
          })}
        </div>
        <p className="text-xs text-[var(--muted)]">
          {SOURCES.find((s) => s.id === mode)?.hint}
        </p>
      </div>

      {needsUrls ? (
        <div className="space-y-2 rounded-xl border border-[var(--line)] bg-[var(--surface)]/80 p-3">
          <div className="flex items-baseline justify-between gap-2">
            <label className="text-sm font-medium">Board URLs</label>
            <span className="text-xs text-[var(--muted)]">
              {cleanUrls.length} added
              {keywords.trim() ? ' · keyword-filtered' : ' · all contract roles'}
            </span>
          </div>
          <div className="space-y-2">
            {urls.map((row, i) => (
              <div key={row.id} className="flex gap-2">
                <input
                  value={row.value}
                  onChange={(e) => updateUrl(row.id, e.target.value)}
                  placeholder="https://boards.greenhouse.io/…"
                  className="w-full rounded-lg border border-[var(--line)] bg-white px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
                  disabled={disabled}
                  aria-label={`Board URL ${i + 1}`}
                />
                <button
                  type="button"
                  onClick={() => removeUrl(row.id)}
                  className="shrink-0 rounded-lg border border-[var(--line)] bg-white px-2.5 text-sm text-[var(--muted)] hover:bg-white disabled:opacity-40"
                  disabled={disabled || urls.length <= 1}
                  aria-label={`Remove URL ${i + 1}`}
                >
                  −
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            onClick={addUrl}
            className="text-sm font-medium text-[var(--accent)]"
            disabled={disabled}
          >
            + Add URL
          </button>
        </div>
      ) : null}

      <div className="space-y-1.5">
        <label className="block text-sm font-medium" htmlFor="minJobs">
          Minimum jobs
        </label>
        <input
          id="minJobs"
          type="number"
          min={1}
          max={200}
          value={minJobs}
          onChange={(e) => setMinJobs(e.target.value)}
          className="w-full rounded-lg border border-[var(--line)] bg-white px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
          disabled={disabled}
        />
      </div>

      {localError ? (
        <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-[var(--danger)]">
          {localError}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={disabled}
        className="w-full rounded-xl bg-[var(--accent)] px-4 py-2.5 text-sm font-semibold text-white hover:opacity-95 disabled:opacity-60"
      >
        {startLabel}
      </button>

      {(scrape?.running || scrape?.message) && (
        <div className="rounded-xl border border-[var(--line)] bg-[var(--surface)] p-3 text-sm">
          <div className="flex items-start justify-between gap-2">
            <p className="font-medium leading-snug text-[var(--ink)]">
              {scrape.message || 'Idle'}
            </p>
            <p className="shrink-0 tabular-nums text-[var(--muted)]">
              {scrape.collected || 0}/{scrape.target || 0}
            </p>
          </div>
          {scrape.running ? (
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white">
              <div
                className="h-full rounded-full bg-[var(--accent)] transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          ) : null}
          {scrape.failed_urls?.length ? (
            <details className="mt-2 text-xs text-[var(--muted)]">
              <summary className="cursor-pointer">
                {scrape.failed_urls.length} failed
              </summary>
              <ul className="mt-1 list-disc space-y-1 pl-4">
                {scrape.failed_urls.map((f) => (
                  <li key={`${f.url}-${f.error}`}>
                    <span className="break-all">{f.url}</span> — {f.error}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      )}
    </form>
  )
}
