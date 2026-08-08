const API = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  if (res.status === 204) return null
  const type = res.headers.get('content-type') || ''
  if (type.includes('application/json')) return res.json()
  return res.text()
}

export function getState() {
  return request('/state')
}

export function startScrape(payload) {
  return request('/scrape/start', { method: 'POST', body: JSON.stringify(payload) })
}

export function saveJob(id, fields) {
  return request(`/jobs/${id}/save`, { method: 'POST', body: JSON.stringify(fields) })
}

export function deleteJob(id) {
  return request(`/jobs/${id}/delete`, { method: 'POST' })
}

export function exportUrl(format) {
  return `${API}/export?format=${format}`
}

export function subscribeProgress(onEvent) {
  const source = new EventSource(`${API}/scrape/progress`)
  source.onmessage = (event) => {
    try {
      onEvent(JSON.parse(event.data))
    } catch {
      /* ignore */
    }
  }
  source.onerror = () => {
    source.close()
  }
  return () => source.close()
}
