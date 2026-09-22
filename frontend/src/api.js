const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

async function apiRequest(endpoint, options = {}) {
  const token = localStorage.getItem('accessToken')
  const isFormData = options.body instanceof FormData

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers: {
      ...(!isFormData && options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token && { Authorization: `Bearer ${token}` }),
      ...options.headers,
    },
  })

  if (!response.ok) {
    let detail = ''
    try {
      const payload = await response.json()
      detail = payload.message || payload.error || ''
    } catch {
      detail = await response.text().catch(() => '')
    }
    throw new Error(detail || `API request failed: ${response.status}`)
  }

  return response
}

export const getJobs = () => apiRequest('/jobs')

export const createJob = (job) =>
  apiRequest('/jobs', {
    method: 'POST',
    body: JSON.stringify(job),
  })

export const getCandidates = (jobId) =>
  apiRequest(`/jobs/${encodeURIComponent(jobId)}/candidates`)

export const uploadResumes = (jobId, files) => {
  const formData = new FormData()
  Array.from(files).forEach((file) => formData.append('files', file))
  return apiRequest(`/jobs/${encodeURIComponent(jobId)}/resumes`, {
    method: 'POST',
    body: formData,
  })
}

export const updateCandidateDecision = (jobId, candidateId, decision) =>
  apiRequest(`/jobs/${encodeURIComponent(jobId)}/candidates/${encodeURIComponent(candidateId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ decision }),
  })

export const getShortlistCsvUrl = (jobId) =>
  `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/shortlist.csv`

export default API_BASE_URL
