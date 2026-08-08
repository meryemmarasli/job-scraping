import { useEffect, useMemo, useState } from 'react'
import { deleteJob, exportUrl, getState, saveJob, startScrape, subscribeProgress } from './api'
import FilterPanel from './components/FilterPanel'
import ReviewScreen from './components/ReviewScreen'
import TopBar from './components/TopBar'

export default function App() {
  const [jobs, setJobs] = useState([])
  const [scrape, setScrape] = useState({
    running: false,
    message: '',
    collected: 0,
    target: 0,
    failed_urls: [],
    finished: false,
  })
  const [index, setIndex] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const visibleJobs = useMemo(
    () => jobs.filter((j) => j.status !== 'deleted'),
    [jobs],
  )
  const approvedCount = useMemo(
    () => jobs.filter((j) => j.status === 'saved').length,
    [jobs],
  )

  async function refresh() {
    const state = await getState()
    setJobs(state.jobs || [])
    setScrape(state.scrape || {})
    return state
  }

  useEffect(() => {
    refresh()
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!scrape.running) return undefined
    const stop = subscribeProgress((payload) => {
      setScrape(payload)
      if (payload.finished) {
        refresh().then(() => setIndex(0))
      }
    })
    return stop
  }, [scrape.running])

  // Keep index in range when jobs change
  useEffect(() => {
    if (!visibleJobs.length) {
      setIndex(0)
      return
    }
    setIndex((i) => Math.min(i, visibleJobs.length - 1))
  }, [visibleJobs.length])

  async function handleStart(form) {
    setError('')
    try {
      await startScrape(form)
      setScrape((s) => ({ ...s, running: true, finished: false, message: 'Starting…' }))
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleSave(jobId, fields) {
    setError('')
    const updated = await saveJob(jobId, fields)
    setJobs((prev) => prev.map((j) => (j.id === jobId ? updated : j)))
    setIndex((i) => Math.min(i + 1, Math.max(visibleJobs.length - 1, 0)))
  }

  async function handleDelete(jobId) {
    setError('')
    await deleteJob(jobId)
    setJobs((prev) =>
      prev.map((j) => (j.id === jobId ? { ...j, status: 'deleted' } : j)),
    )
  }

  function download(format) {
    const a = document.createElement('a')
    a.href = exportUrl(format)
    a.download = format === 'csv' ? 'approved-jobs.csv' : 'approved-jobs.json'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-[var(--muted)]">
        Loading…
      </div>
    )
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <div className="shrink-0 border-b border-[var(--line)] bg-white/80 px-4 py-3 backdrop-blur md:px-6">
        <TopBar
          approvedCount={approvedCount}
          totalVisible={visibleJobs.length}
          onDownload={download}
          scraping={scrape.running}
        />
      </div>

      {error && visibleJobs.length ? (
        <div className="shrink-0 border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-[var(--danger)]">
          {error}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* Left: filters */}
        <aside className="flex w-full shrink-0 flex-col border-b border-[var(--line)] bg-white lg:w-[380px] lg:border-b-0 lg:border-r">
          <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
            <FilterPanel
              onStart={handleStart}
              disabled={scrape.running}
              scrape={scrape}
            />
          </div>
        </aside>

        {/* Right: annotation / review */}
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-[var(--surface)]/60 p-4 md:p-6">
          <ReviewScreen
            jobs={visibleJobs}
            index={index}
            setIndex={setIndex}
            onSave={handleSave}
            onDelete={handleDelete}
            scrape={scrape}
            requestError={error}
          />
        </main>
      </div>
    </div>
  )
}
