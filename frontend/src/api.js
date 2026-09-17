const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || '';

async function apiRequest(endpoint, options = {}) {
  const token = localStorage.getItem('accessToken');

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token && { Authorization: `Bearer ${token}` }),
      ...options.headers,
    },
  });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }

  return response;
}

export const getJobs = () => apiRequest('/jobs');

export const getCandidates = (jobId) =>
  apiRequest(`/jobs/${jobId}/candidates`);

export const updateCandidateDecision = (jobId, candidateId, decision) =>
  apiRequest(`/jobs/${jobId}/candidates/${candidateId}`, {
    method: 'PATCH',
    body: JSON.stringify({ decision }),
  });

export const getShortlistCsvUrl = (jobId) =>
  `${API_BASE_URL}/jobs/${jobId}/shortlist.csv`;

export default API_BASE_URL;
