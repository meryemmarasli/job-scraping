import { useEffect, useState } from 'react'

const STATUS_STYLES = {
  unreviewed: 'bg-amber-50 text-amber-800 border-amber-200',
  saved: 'bg-[var(--accent-soft)] text-[var(--accent)] border-emerald-200',
  deleted: 'bg-red-50 text-red-700 border-red-200',
}

const EMPTY_FORM = {
  title: '',
  company: '',
  client_partner: '',
  location: '',
  employment_type: 'contract',
  pay_rate: '',
  salary: '',
  hours_per_week: '',
  duration: '',
  work_mode: 'remote',
  languages: '',
  domain: '',
  task_type: '',
  responsibilities: '',
  requirements: '',
  preferred: '',
  tools_skills: '',
  screening: '',
  description: '',
  url: '',
  posted_date: '',
  notes: '',
  status: 'unreviewed',
}

function Field({ label, children }) {
  return (
    <label className="block text-sm">
      <span className="font-medium">{label}</span>
      {children}
    </label>
  )
}

function TextInput({ value, onChange, className = '' }) {
  return (
    <input
      value={value}
      onChange={onChange}
      className={`mt-1 w-full rounded-lg border border-[var(--line)] px-3 py-2 outline-none focus:border-[var(--accent)] ${className}`}
    />
  )
}

function TextArea({ value, onChange, rows = 3 }) {
  return (
    <textarea
      value={value}
      onChange={onChange}
      rows={rows}
      className="mt-1 w-full rounded-lg border border-[var(--line)] px-3 py-2 outline-none focus:border-[var(--accent)]"
    />
  )
}

function FailurePanel({ scrape, requestError }) {
  const failed = scrape?.failed_urls || []
  const hasError = Boolean(requestError || scrape?.error)
  const finishedEmpty =
    scrape?.finished && !scrape?.running && (scrape?.collected || 0) === 0

  if (!hasError && !finishedEmpty && !failed.length) return null

  const title = requestError
    ? 'Request failed'
    : scrape?.error
      ? 'Collection failed'
      : finishedEmpty
        ? 'No contract jobs found'
        : 'Some sources failed'

  const detail =
    requestError ||
    scrape?.error ||
    scrape?.message ||
    'Nothing matched as contract roles.'

  return (
    <div className="rounded-2xl border border-red-200 bg-red-50 p-5 text-left shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-wide text-[var(--danger)]">
        Annotation status
      </p>
      <h3 className="mt-1 text-lg font-semibold text-[var(--danger)]">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-[var(--ink)]">{detail}</p>
      {failed.length ? (
        <details className="mt-3 text-sm text-[var(--muted)]" open={finishedEmpty || hasError}>
          <summary className="cursor-pointer font-medium text-[var(--danger)]">
            {failed.length} failed source{failed.length === 1 ? '' : 's'}
          </summary>
          <ul className="mt-2 max-h-48 list-disc space-y-1 overflow-y-auto pl-5">
            {failed.map((f) => (
              <li key={`${f.url}-${f.error}`}>
                <span className="break-all font-medium text-[var(--ink)]">{f.url}</span>
                <span className="text-[var(--muted)]"> — {f.error}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  )
}

export default function ReviewScreen({
  jobs,
  index,
  setIndex,
  onSave,
  onDelete,
  scrape,
  requestError,
}) {
  const job = jobs[index]
  const [form, setForm] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!job) {
      setForm(null)
      return
    }
    setForm({
      ...EMPTY_FORM,
      title: job.title || '',
      company: job.company || '',
      client_partner: job.client_partner || '',
      location: job.location || '',
      employment_type: job.employment_type || 'contract',
      pay_rate: job.pay_rate || job.salary || '',
      salary: job.salary || job.pay_rate || '',
      hours_per_week: job.hours_per_week || '',
      duration: job.duration || '',
      work_mode: job.work_mode || 'remote',
      languages: job.languages || '',
      domain: job.domain || '',
      task_type: job.task_type || '',
      responsibilities: job.responsibilities || '',
      requirements: job.requirements || '',
      preferred: job.preferred || '',
      tools_skills: job.tools_skills || '',
      screening: job.screening || '',
      description: job.description || '',
      url: job.url || '',
      posted_date: job.posted_date || '',
      notes: job.notes || '',
      status: job.status || 'unreviewed',
    })
  }, [job?.id])

  useEffect(() => {
    function onKey(e) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
        return
      }
      if (e.key === 'ArrowRight' || e.key === 'j') {
        setIndex((i) => Math.min(i + 1, Math.max(jobs.length - 1, 0)))
      }
      if (e.key === 'ArrowLeft' || e.key === 'k') {
        setIndex((i) => Math.max(i - 1, 0))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [jobs.length, setIndex])

  if (!jobs.length) {
    if (scrape?.running) {
      return (
        <div className="flex h-full min-h-[320px] items-center justify-center rounded-2xl border border-[var(--line)] bg-white/80 p-8 text-center">
          <div className="max-w-md">
            <h2 className="text-lg font-semibold">Searching for contract jobs…</h2>
            <p className="mt-2 text-sm text-[var(--muted)]">
              {scrape.message || 'Collecting listings.'}
            </p>
            <p className="mt-3 tabular-nums text-sm text-[var(--muted)]">
              {scrape.collected || 0}/{scrape.target || 0}
            </p>
          </div>
        </div>
      )
    }

    const showFailure =
      requestError ||
      scrape?.error ||
      (scrape?.finished && (scrape?.collected || 0) === 0) ||
      (scrape?.failed_urls || []).length > 0

    if (showFailure) {
      return (
        <div className="mx-auto flex h-full min-h-[320px] max-w-xl flex-col justify-center gap-4 p-2">
          <FailurePanel scrape={scrape} requestError={requestError} />
          <p className="text-center text-sm text-[var(--muted)]">
            Adjust filters on the left, then try again. Only contract / freelance / temporary
            roles are kept.
          </p>
        </div>
      )
    }

    return (
      <div className="flex h-full min-h-[320px] items-center justify-center rounded-2xl border border-dashed border-[var(--line)] bg-white/70 p-8 text-center">
        <div>
          <h2 className="text-lg font-semibold">Annotation panel</h2>
          <p className="mt-2 max-w-sm text-sm text-[var(--muted)]">
            Contract labeling roles will appear here. Scrape a board without keywords, or search
            online with keywords.
          </p>
        </div>
      </div>
    )
  }

  if (!form) return null

  function setField(key, value) {
    setForm((f) => {
      const next = { ...f, [key]: value }
      if (key === 'pay_rate' && !f.salary) next.salary = value
      return next
    })
  }

  async function save() {
    setSaving(true)
    try {
      await onSave(job.id, form)
    } finally {
      setSaving(false)
    }
  }

  async function skip() {
    setSaving(true)
    try {
      await onDelete(job.id)
      setIndex((i) => Math.min(i, Math.max(jobs.length - 2, 0)))
    } finally {
      setSaving(false)
    }
  }

  const previewBits = [
    job.company,
    job.domain,
    job.task_type,
    job.pay_rate || job.salary,
    job.hours_per_week,
    job.work_mode,
    job.location,
  ].filter(Boolean)

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4">
      {(requestError || scrape?.error || (scrape?.failed_urls || []).length > 0) && (
        <FailurePanel scrape={scrape} requestError={requestError} />
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Annotation</h2>
          <p className="text-sm text-[var(--muted)]">
            Job {index + 1} of {jobs.length}
            <span className="mx-2">·</span>
            <kbd className="rounded border border-[var(--line)] bg-white px-1.5 text-xs">←</kbd>{' '}
            <kbd className="rounded border border-[var(--line)] bg-white px-1.5 text-xs">→</kbd>
            <span className="mx-2">·</span>
            <span className="text-[var(--accent)]">Contract only</span>
          </p>
        </div>
        <span
          className={`rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wide ${STATUS_STYLES[job.status] || STATUS_STYLES.unreviewed}`}
        >
          {job.status}
        </span>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="rounded-2xl border border-[var(--line)] bg-white p-4 shadow-sm">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Listing preview
          </h3>
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer"
            className="mt-3 block text-xl font-semibold text-[var(--accent)] hover:underline"
          >
            {job.title || 'Untitled job'}
          </a>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {previewBits.join(' · ') || '—'}
          </p>
          {job.client_partner ? (
            <p className="mt-2 text-sm text-[var(--muted)]">
              Partner: <span className="text-[var(--ink)]">{job.client_partner}</span>
            </p>
          ) : null}
          <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed">
            {job.description || 'No description extracted.'}
          </p>
          {job.url ? (
            <a
              href={job.url}
              target="_blank"
              rel="noreferrer"
              className="mt-4 inline-block text-sm font-medium text-[var(--accent)] underline"
            >
              Open original listing
            </a>
          ) : null}
        </section>

        <section className="rounded-2xl border border-[var(--line)] bg-white p-4 shadow-sm">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
            Edit fields
          </h3>
          <p className="mt-1 text-xs text-[var(--muted)]">
            Structured for AI labeling / expert contributor roles (Mercor, Surge, Scale-style).
          </p>

          <div className="mt-4 space-y-5">
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                Identity
              </p>
              <Field label="Title">
                <TextInput value={form.title} onChange={(e) => setField('title', e.target.value)} />
              </Field>
              <Field label="Company / platform">
                <TextInput value={form.company} onChange={(e) => setField('company', e.target.value)} />
              </Field>
              <Field label="Client / partner lab">
                <TextInput
                  value={form.client_partner}
                  onChange={(e) => setField('client_partner', e.target.value)}
                />
              </Field>
              <Field label="URL">
                <TextInput value={form.url} onChange={(e) => setField('url', e.target.value)} />
              </Field>
              <Field label="Posted date">
                <TextInput
                  value={form.posted_date}
                  onChange={(e) => setField('posted_date', e.target.value)}
                />
              </Field>
            </div>

            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                Engagement
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Employment type">
                  <select
                    value={form.employment_type}
                    onChange={(e) => setField('employment_type', e.target.value)}
                    className="mt-1 w-full rounded-lg border border-[var(--line)] px-3 py-2 outline-none focus:border-[var(--accent)]"
                  >
                    <option value="contract">contract</option>
                    <option value="freelance">freelance</option>
                    <option value="temporary">temporary</option>
                    <option value="part-time">part-time</option>
                    <option value="other">other</option>
                  </select>
                </Field>
                <Field label="Work mode">
                  <select
                    value={form.work_mode}
                    onChange={(e) => setField('work_mode', e.target.value)}
                    className="mt-1 w-full rounded-lg border border-[var(--line)] px-3 py-2 outline-none focus:border-[var(--accent)]"
                  >
                    <option value="remote">remote</option>
                    <option value="async remote">async remote</option>
                    <option value="hybrid">hybrid</option>
                    <option value="onsite">onsite</option>
                  </select>
                </Field>
                <Field label="Pay rate">
                  <TextInput
                    value={form.pay_rate}
                    onChange={(e) => setField('pay_rate', e.target.value)}
                  />
                </Field>
                <Field label="Hours / week">
                  <TextInput
                    value={form.hours_per_week}
                    onChange={(e) => setField('hours_per_week', e.target.value)}
                  />
                </Field>
                <Field label="Duration">
                  <TextInput
                    value={form.duration}
                    onChange={(e) => setField('duration', e.target.value)}
                  />
                </Field>
                <Field label="Location / geo">
                  <TextInput
                    value={form.location}
                    onChange={(e) => setField('location', e.target.value)}
                  />
                </Field>
                <Field label="Languages">
                  <TextInput
                    value={form.languages}
                    onChange={(e) => setField('languages', e.target.value)}
                  />
                </Field>
              </div>
            </div>

            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                Role
              </p>
              <Field label="Domain / expertise">
                <TextInput value={form.domain} onChange={(e) => setField('domain', e.target.value)} />
              </Field>
              <Field label="Task type">
                <TextInput
                  value={form.task_type}
                  onChange={(e) => setField('task_type', e.target.value)}
                />
              </Field>
              <Field label="Tools / skills">
                <TextInput
                  value={form.tools_skills}
                  onChange={(e) => setField('tools_skills', e.target.value)}
                />
              </Field>
              <Field label="Screening steps">
                <TextInput
                  value={form.screening}
                  onChange={(e) => setField('screening', e.target.value)}
                />
              </Field>
              <Field label="Responsibilities">
                <TextArea
                  value={form.responsibilities}
                  onChange={(e) => setField('responsibilities', e.target.value)}
                  rows={3}
                />
              </Field>
              <Field label="Requirements">
                <TextArea
                  value={form.requirements}
                  onChange={(e) => setField('requirements', e.target.value)}
                  rows={3}
                />
              </Field>
              <Field label="Preferred / nice to have">
                <TextArea
                  value={form.preferred}
                  onChange={(e) => setField('preferred', e.target.value)}
                  rows={2}
                />
              </Field>
            </div>

            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
                Full text & review
              </p>
              <Field label="Description">
                <TextArea
                  value={form.description}
                  onChange={(e) => setField('description', e.target.value)}
                  rows={6}
                />
              </Field>
              <Field label="Notes">
                <TextArea
                  value={form.notes}
                  onChange={(e) => setField('notes', e.target.value)}
                  rows={2}
                />
              </Field>
              <Field label="Status">
                <select
                  value={form.status}
                  onChange={(e) => setField('status', e.target.value)}
                  className="mt-1 w-full rounded-lg border border-[var(--line)] px-3 py-2 outline-none focus:border-[var(--accent)]"
                >
                  <option value="unreviewed">unreviewed</option>
                  <option value="saved">saved</option>
                  <option value="deleted">deleted</option>
                </select>
              </Field>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
              disabled={index <= 0}
              className="rounded-lg border border-[var(--line)] px-3 py-2 text-sm disabled:opacity-40"
            >
              ← Back
            </button>
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
            >
              Save
            </button>
            <button
              type="button"
              onClick={skip}
              disabled={saving}
              className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm font-medium text-[var(--danger)] disabled:opacity-60"
            >
              Delete / Skip
            </button>
            <button
              type="button"
              onClick={() => setIndex((i) => Math.min(jobs.length - 1, i + 1))}
              disabled={index >= jobs.length - 1}
              className="rounded-lg border border-[var(--line)] px-3 py-2 text-sm disabled:opacity-40"
            >
              Next →
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}
