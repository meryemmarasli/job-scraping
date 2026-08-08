export default function TopBar({
  approvedCount,
  totalVisible,
  onDownload,
  scraping,
}) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--accent)]">
          Job Scraper
        </p>
        <h1 className="text-xl font-semibold tracking-tight text-[var(--ink)] md:text-2xl">
          Scrape, annotate, export
        </h1>
        <p className="mt-0.5 text-sm text-[var(--muted)]">
          {approvedCount} approved · {totalVisible} in queue
          {scraping ? ' · scraping…' : ''}
        </p>
      </div>

      <div className="flex overflow-hidden rounded-lg border border-[var(--line)] bg-white">
        <button
          type="button"
          onClick={() => onDownload('json')}
          className="px-3 py-2 text-sm font-medium text-[var(--accent)] hover:bg-[var(--accent-soft)]"
          title="Download approved jobs as JSON"
        >
          Download JSON
        </button>
        <button
          type="button"
          onClick={() => onDownload('csv')}
          className="border-l border-[var(--line)] px-3 py-2 text-sm font-medium text-[var(--accent)] hover:bg-[var(--accent-soft)]"
          title="Download approved jobs as CSV"
        >
          CSV
        </button>
      </div>
    </header>
  )
}
