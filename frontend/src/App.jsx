import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import API_URL, {
  createJob,
  getCandidates,
  getJobs,
  getShortlistCsvUrl,
  updateCandidateDecision,
  uploadResumes,
} from './api'

const sampleCandidates = [
  {
    id: 'demo-sarah',
    parsed_profile: {
      name: 'Sarah Jenkins',
      email: 'sarah.jenkins@example.com',
      phone: '+1 (555) 234-5678',
      skills: ['AWS', 'Lambda', 'API Gateway', 'DynamoDB', 'Docker', 'Python'],
      job_titles: ['Cloud Engineer', 'Cloud Support Associate'],
      experience_years: 9.7,
      links: { linkedin: 'https://linkedin.com', github: 'https://github.com' },
      confidence: { overall: 0.95 },
      flags: [],
    },
    status: 'Shortlisted',
  },
  {
    id: 'demo-michael',
    parsed_profile: {
      name: 'Michael Rodriguez',
      email: 'michael.rodriguez@example.com',
      phone: '+1 (555) 901-2233',
      skills: ['React', 'JavaScript', 'TypeScript', 'Node.js', 'Git'],
      job_titles: ['Frontend Engineer', 'UI Developer'],
      experience_years: 5.2,
      links: { linkedin: 'https://linkedin.com', github: 'https://github.com' },
      confidence: { overall: 0.88 },
      flags: [],
    },
    status: 'New',
  },
  {
    id: 'demo-priya',
    parsed_profile: {
      name: 'Priya Sharma',
      email: 'priya.sharma@example.com',
      phone: '+91 98765 43210',
      skills: ['Python', 'Amazon Comprehend', 'Textract', 'S3', 'SQL'],
      job_titles: ['Machine Learning Engineer', 'Data Analyst'],
      experience_years: 4.6,
      links: { linkedin: 'https://linkedin.com', github: 'https://github.com' },
      confidence: { overall: 0.91 },
      flags: ['MISSING_PORTFOLIO'],
    },
    status: 'Reviewing',
  },
]

const sampleJobs = [
  { id: 'sample-cloud', title: 'Cloud Support Engineer', department: 'Cloud Operations', location: 'Remote · India', applicants: 48, color: 'purple' },
  { id: 'sample-frontend', title: 'Frontend Engineer', department: 'Engineering', location: 'Remote · US', applicants: 36, color: 'cyan' },
  { id: 'sample-ml', title: 'Machine Learning Engineer', department: 'Artificial Intelligence', location: 'Bengaluru · Hybrid', applicants: 24, color: 'orange' },
]

const currentDate = new Intl.DateTimeFormat('en-US', {
  weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
}).format(new Date()).toUpperCase()

function Icon({ name, size = 18 }) {
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    briefcase: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18" /></>,
    users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /></>,
    star: <path d="m12 3 2.78 5.63 6.22.9-4.5 4.38 1.06 6.19L12 17.18l-5.56 2.92 1.06-6.19L3 9.53l6.22-.9L12 3Z" />,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.93 4.93l2.12 2.12M16.95 16.95l2.12 2.12M2 12h3M19 12h3M4.93 19.07l2.12-2.12M16.95 7.05l2.12-2.12" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></>,
    upload: <><path d="M12 16V4M7 9l5-5 5 5M4 20h16" /></>,
    download: <><path d="M12 4v12M7 11l5 5 5-5M4 20h16" /></>,
    file: <><path d="M6 2h8l4 4v16H6z" /><path d="M14 2v5h5M9 13h6M9 17h6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    menu: <><path d="M4 6h16M4 12h16M4 18h16" /></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function listFromResponse(data, key) {
  if (Array.isArray(data)) return data
  if (Array.isArray(data?.[key])) return data[key]
  if (Array.isArray(data?.items)) return data.items
  if (Array.isArray(data?.data)) return data.data
  if (Array.isArray(data?.data?.[key])) return data.data[key]
  return []
}

function candidateId(candidate) {
  return candidate.id ?? candidate.candidate_id ?? candidate.candidateId
}

async function responseJson(response) {
  if (response.status === 204) return {}
  return response.json().catch(() => ({}))
}

function CandidateTable({ candidates, pendingId, onStatus }) {
  if (!candidates.length) return <div className="empty-state">No candidates match the selected filters.</div>
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Candidate</th><th>Role</th><th>Experience</th><th>Skills</th><th>Score</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>
          {candidates.map((candidate) => {
            const profile = candidate.parsed_profile || {}
            const confidence = profile.confidence?.overall != null ? Math.round(profile.confidence.overall * 100) : null
            const id = candidateId(candidate)
            return (
              <tr key={id}>
                <td><div className="candidate-name"><div className="avatar">{(profile.name || 'NA').split(' ').map((part) => part[0]).join('').slice(0, 2)}</div><div><strong>{profile.name || 'Unknown Candidate'}</strong><small>{profile.email || 'No email'}</small><small>{profile.phone || 'No phone'}</small></div></div></td>
                <td><div className="role-cell">{(profile.job_titles || []).length ? profile.job_titles.join(' / ') : 'Not available'}<small>{profile.links?.linkedin && <a href={profile.links.linkedin} target="_blank" rel="noreferrer">LinkedIn</a>}{profile.links?.linkedin && profile.links?.github && ' · '}{profile.links?.github && <a href={profile.links.github} target="_blank" rel="noreferrer">GitHub</a>}</small></div></td>
                <td>{profile.experience_years != null ? `${profile.experience_years} yrs` : 'N/A'}</td>
                <td><div className="skill-list">{(profile.skills || []).slice(0, 3).map((skill) => <span key={skill}>{skill}</span>)}{(profile.skills || []).length > 3 && <small>+{profile.skills.length - 3}</small>}</div></td>
                <td><div className="score-ring" style={{ '--score': `${(confidence ?? 0) * 3.6}deg` }}><span>{confidence != null ? `${confidence}%` : 'N/A'}</span></div></td>
                <td><span className={`status ${(candidate.status || 'New').toLowerCase()}`}>{candidate.status || 'New'}</span></td>
                <td><div className="row-actions"><button className="shortlist-button" onClick={() => onStatus(id, 'Shortlisted')} disabled={pendingId === id || candidate.status === 'Shortlisted'}>Shortlist</button><button className="reject-button" onClick={() => onStatus(id, 'Rejected')} disabled={pendingId === id || candidate.status === 'Rejected'}>Reject</button></div></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function App() {
  const [candidates, setCandidates] = useState(sampleCandidates)
  const [jobs, setJobs] = useState(sampleJobs)
  const [selectedJobId, setSelectedJobId] = useState(sampleJobs[0].id)
  const [apiLoading, setApiLoading] = useState(Boolean(API_URL))
  const [apiOffline, setApiOffline] = useState(!API_URL)
  const [apiReady, setApiReady] = useState(false)
  const [apiError, setApiError] = useState('')
  const [notice, setNotice] = useState('')
  const [pendingId, setPendingId] = useState(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('All statuses')
  const [activePage, setActivePage] = useState('Dashboard')
  const [selectedFiles, setSelectedFiles] = useState([])
  const [jobForm, setJobForm] = useState({ title: '', department: '', location: '', description: '' })
  const fileInputRef = useRef(null)

  const liveApi = Boolean(API_URL) && !apiOffline

  useEffect(() => {
    if (!API_URL) return undefined
    let cancelled = false
    async function loadJobs() {
      try {
        const response = await getJobs()
        const payload = await responseJson(response)
        const list = listFromResponse(payload, 'jobs')
        if (!cancelled && list.length) {
          setJobs(list)
          setSelectedJobId(list[0]?.job_id ?? list[0]?.jobId ?? list[0]?.id)
          setApiOffline(false)
          setApiReady(true)
          setApiError('')
        } else if (!cancelled) {
          setApiOffline(true)
          setApiReady(false)
          setApiError('')
        }
      } catch (error) {
        if (!cancelled) {
          setApiOffline(true)
          setApiReady(false)
          setApiError('')
        }
      } finally {
        if (!cancelled) setApiLoading(false)
      }
    }
    loadJobs()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!liveApi || !apiReady || selectedJobId == null) return undefined
    let cancelled = false
    async function loadCandidates() {
      setApiLoading(true)
      try {
        const response = await getCandidates(selectedJobId)
        const payload = await responseJson(response)
        const list = listFromResponse(payload, 'candidates')
        if (!cancelled) setCandidates(list)
      } catch {
        if (!cancelled) {
          setApiOffline(true)
          setApiError('Candidate API could not be reached. Demo data remains available for the frontend walkthrough.')
        }
      } finally {
        if (!cancelled) setApiLoading(false)
      }
    }
    loadCandidates()
    return () => { cancelled = true }
  }, [apiReady, liveApi, selectedJobId])

  const filteredCandidates = useMemo(() => candidates.filter((candidate) => {
    const profile = candidate.parsed_profile || {}
    const searchable = [profile.name, profile.email, profile.phone, ...(profile.job_titles || []), ...(profile.skills || [])].filter(Boolean).join(' ').toLowerCase()
    const pageStatus = activePage === 'Shortlisted' ? 'Shortlisted' : statusFilter
    return searchable.includes(search.toLowerCase()) && (pageStatus === 'All statuses' || candidate.status === pageStatus)
  }), [activePage, candidates, search, statusFilter])

  const selectedJob = jobs.find((job) => (job.id ?? job.job_id ?? job.jobId) === selectedJobId) || jobs[0]

  const updateStatus = async (id, status) => {
    if (liveApi && selectedJobId != null) {
      try {
        setPendingId(id)
        await updateCandidateDecision(selectedJobId, id, status === 'Shortlisted' ? 'SHORTLIST' : 'REJECT')
      } catch {
        setApiOffline(true)
        setApiError('Decision API was unavailable. The change was applied locally for this demo session.')
      } finally {
        setPendingId(null)
      }
    }
    setCandidates((current) => current.map((candidate) => candidateId(candidate) === id ? { ...candidate, status } : candidate))
    setNotice(`Candidate marked as ${status}.`)
  }

  const submitJob = async (event) => {
    event.preventDefault()
    if (!jobForm.title.trim() || !jobForm.description.trim()) return
    setApiLoading(true)
    let created = null
    let savedLive = false
    if (liveApi) {
      try {
        const response = await createJob(jobForm)
        const payload = await responseJson(response)
        created = payload.job || payload.data || payload
        savedLive = true
      } catch {
        setApiOffline(true)
        setApiError('Create-job API was unavailable. The job was added locally in demo mode.')
      }
    }
    if (!created?.id && !created?.job_id && !created?.jobId) {
      created = { ...jobForm, id: `demo-${Date.now()}`, applicants: 0, color: 'cyan' }
    }
    const id = created.id ?? created.job_id ?? created.jobId
    setJobs((current) => [created, ...current])
    setSelectedJobId(id)
    setJobForm({ title: '', department: '', location: '', description: '' })
    setNotice(`“${created.title || created.name}” created successfully${savedLive ? '.' : ' in demo mode.'}`)
    setApiLoading(false)
  }

  const processResumes = async () => {
    if (!selectedFiles.length || !selectedJobId) return
    setApiLoading(true)
    if (liveApi) {
      try {
        await uploadResumes(selectedJobId, selectedFiles)
        setNotice(`${selectedFiles.length} resume(s) uploaded for AI processing.`)
      } catch {
        setApiOffline(true)
        setApiError('Resume API was unavailable. Files were validated locally; sample analysis remains visible for the demo.')
        setNotice(`${selectedFiles.length} resume file(s) validated in demo mode.`)
      }
    } else {
      setNotice(`${selectedFiles.length} resume file(s) validated in demo mode. Sample AI results are available under Candidates.`)
    }
    setSelectedFiles([])
    if (fileInputRef.current) fileInputRef.current.value = ''
    setApiLoading(false)
  }

  const downloadCsv = () => {
    if (liveApi && selectedJobId != null) {
      const link = document.createElement('a')
      link.href = getShortlistCsvUrl(selectedJobId)
      link.download = 'talentflow-shortlist.csv'
      link.click()
      return
    }
    const shortlisted = candidates.filter((candidate) => candidate.status === 'Shortlisted')
    const rows = [['Candidate', 'Job Titles', 'Experience', 'Skills', 'Email', 'Phone', 'Confidence', 'Flags'], ...shortlisted.map((candidate) => {
      const profile = candidate.parsed_profile || {}
      return [profile.name || '', (profile.job_titles || []).join(' / '), profile.experience_years ?? '', (profile.skills || []).join(' / '), profile.email || '', profile.phone || '', profile.confidence?.overall != null ? `${Math.round(profile.confidence.overall * 100)}%` : '', (profile.flags || []).join(' / ')]
    })]
    const csv = rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'talentflow-shortlist.csv'
    link.click()
    URL.revokeObjectURL(url)
  }

  const filters = (
    <div className="table-toolbar">
      <label className="search-field"><Icon name="search" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search candidates or skills" /></label>
      {activePage !== 'Shortlisted' && <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option>All statuses</option><option>New</option><option>Reviewing</option><option>Shortlisted</option><option>Rejected</option></select>}
      <span className="result-count">{filteredCandidates.length} result(s)</span>
    </div>
  )

  const renderDashboard = () => (
    <>
      <section className="summary-grid">
        {[['Total candidates', String(candidates.length), 'users', 'cyan'], ['Active jobs', String(jobs.length), 'briefcase', 'purple'], ['Shortlisted', String(candidates.filter((candidate) => candidate.status === 'Shortlisted').length), 'star', 'green']].map(([label, value, icon, color]) => <div className="summary-card" key={label}><span className={`summary-icon ${color}`}><Icon name={icon} /></span><span className="summary-label">{label}</span><strong className="summary-value">{value}</strong><small className="summary-change"><span>Current pipeline</span></small></div>)}
      </section>
      <div className="dashboard-grid">
        <section className="panel"><div className="panel-heading"><div><h2>Active jobs</h2><p>Track open roles and select a hiring pipeline</p></div><button className="text-button" onClick={() => setActivePage('Jobs')}>Manage jobs →</button></div><div className="job-list">{jobs.slice(0, 4).map((job) => { const id = job.id ?? job.job_id ?? job.jobId; return <button className={`job-row ${id === selectedJobId ? 'selected' : ''}`} key={id} onClick={() => setSelectedJobId(id)}><span className={`job-icon ${job.color || 'purple'}`}><Icon name="briefcase" /></span><span className="job-info"><strong>{job.title || job.name || 'Untitled job'}</strong><small>{job.department || job.location || 'Open position'}</small></span><b>{job.applicants ?? 0}</b></button> })}</div></section>
        <section className="panel"><div className="panel-heading"><div><h2>Pipeline preview</h2><p>Top AI-ranked candidates</p></div><button className="text-button" onClick={() => setActivePage('Candidates')}>View all →</button></div><div className="candidate-preview">{candidates.slice(0, 3).map((candidate) => { const profile = candidate.parsed_profile || {}; return <div key={candidateId(candidate)}><span className="avatar">{(profile.name || 'NA').split(' ').map((part) => part[0]).join('').slice(0, 2)}</span><span><strong>{profile.name}</strong><small>{(profile.job_titles || [])[0]}</small></span><b>{profile.confidence?.overall != null ? `${Math.round(profile.confidence.overall * 100)}%` : 'N/A'}</b></div> })}</div></section>
      </div>
    </>
  )

  const renderJobs = () => (
    <div className="jobs-page-grid">
      <section className="panel form-panel"><div className="panel-heading"><div><h2>Create a job</h2><p>Add the job description used by the scoring engine</p></div></div><form onSubmit={submitJob} className="job-form"><label>Job title<input required value={jobForm.title} onChange={(event) => setJobForm({ ...jobForm, title: event.target.value })} placeholder="e.g. Cloud Support Engineer" /></label><div className="form-row"><label>Department<input value={jobForm.department} onChange={(event) => setJobForm({ ...jobForm, department: event.target.value })} placeholder="Cloud Operations" /></label><label>Location<input value={jobForm.location} onChange={(event) => setJobForm({ ...jobForm, location: event.target.value })} placeholder="Remote · India" /></label></div><label>Job description<textarea required rows="7" value={jobForm.description} onChange={(event) => setJobForm({ ...jobForm, description: event.target.value })} placeholder="Paste the responsibilities, required skills and experience here…" /></label><button className="primary-button" disabled={apiLoading}><Icon name="briefcase" />{apiLoading ? 'Saving…' : 'Create job & analyse JD'}</button></form></section>
      <div className="jobs-side">
        <section className="panel"><div className="panel-heading"><div><h2>Active jobs</h2><p>Select a job before uploading resumes</p></div></div><div className="job-list">{jobs.map((job) => { const id = job.id ?? job.job_id ?? job.jobId; return <button className={`job-row ${id === selectedJobId ? 'selected' : ''}`} key={id} onClick={() => setSelectedJobId(id)}><span className={`job-icon ${job.color || 'purple'}`}><Icon name="briefcase" /></span><span className="job-info"><strong>{job.title || job.name}</strong><small>{job.department || job.location || 'Open position'}</small></span><b>{job.applicants ?? 0}</b></button> })}</div></section>
        <section className="panel upload-panel"><div className="upload-heading"><span className="upload-orb"><Icon name="upload" /></span><div><h2>Upload resumes</h2><p>{selectedJob ? `Selected job: ${selectedJob.title || selectedJob.name}` : 'Select a job first'}</p></div></div><button type="button" className="dropzone" onClick={() => fileInputRef.current?.click()}><Icon name="file" size={23} /><strong>{selectedFiles.length ? `${selectedFiles.length} file(s) selected` : 'Choose PDF or DOCX resumes'}</strong><span>Multiple files supported</span></button><input ref={fileInputRef} className="hidden-input" type="file" multiple accept=".pdf,.doc,.docx" onChange={(event) => setSelectedFiles(Array.from(event.target.files || []))} />{selectedFiles.length > 0 && <div className="selected-files">{selectedFiles.map((file) => <span key={`${file.name}-${file.size}`}><Icon name="check" size={14} />{file.name}</span>)}</div>}<button className="primary-button full-button" onClick={processResumes} disabled={!selectedFiles.length || !selectedJobId || apiLoading}>{apiLoading ? 'Processing…' : 'Upload & process resumes'}</button></section>
      </div>
    </div>
  )

  const renderCandidates = () => (
    <section className="panel candidates-panel"><div className="panel-heading candidate-heading"><div><h2>{activePage === 'Shortlisted' ? 'Shortlisted candidates' : 'Candidate pipeline'}</h2><p>{activePage === 'Shortlisted' ? 'Candidates selected for the next hiring stage' : 'Search, review and manage AI-ranked applicants'}</p></div><button className="outline-button" onClick={downloadCsv}><Icon name="download" /> Download shortlist</button></div>{filters}{apiLoading && liveApi ? <div className="empty-state">Loading candidates…</div> : <CandidateTable candidates={filteredCandidates} pendingId={pendingId} onStatus={updateStatus} />}</section>
  )

  const renderSettings = () => (
    <section className="panel settings-panel"><h2>Application status</h2><div className="setting-row"><span>Frontend</span><strong className="online">Running</strong></div><div className="setting-row"><span>Data source</span><strong>{liveApi ? 'Live API' : 'Demo data'}</strong></div><div className="setting-row"><span>API base URL</span><code>{API_URL || 'Not configured'}</code></div><p>Set <code>VITE_API_BASE_URL</code> in the frontend environment to connect this interface to API Gateway or the deployed backend.</p></section>
  )

  const pageDescriptions = {
    Dashboard: 'Here’s what’s happening with your talent pipeline today.',
    Jobs: 'Create a job description and upload resumes for AI processing.',
    Candidates: 'Review structured profiles, confidence scores and skill matches.',
    Shortlisted: 'Manage candidates selected for the next hiring stage.',
    Settings: 'Check frontend and API configuration.',
  }

  return (
    <div className="app-shell">
      <aside className="sidebar"><div className="brand"><span className="brand-mark">✦</span><span>talent<span className="brand-accent">flow</span></span></div><div className="workspace-label">WORKSPACE</div><nav className="main-nav" aria-label="Main navigation">{[['Dashboard', 'grid'], ['Jobs', 'briefcase'], ['Candidates', 'users'], ['Shortlisted', 'star']].map(([label, icon]) => <button key={label} className={activePage === label ? 'nav-item active' : 'nav-item'} onClick={() => setActivePage(label)}><Icon name={icon} /><span>{label}</span></button>)}</nav><div className="sidebar-bottom"><button className={activePage === 'Settings' ? 'nav-item active' : 'nav-item'} onClick={() => setActivePage('Settings')}><Icon name="settings" /><span>Settings</span></button></div></aside>
      <main className="main-content"><header className="topbar"><button className="mobile-menu" aria-label="Open menu"><Icon name="menu" /></button><div className="breadcrumb"><span>Workspace</span><b>/</b><strong>{activePage}</strong></div><div className="top-actions"><Icon name="search" /><Icon name="bell" /><strong>PP</strong></div></header><div className="content-wrap"><div className="page-heading"><div><div className="eyebrow">{currentDate}</div><h1>{activePage === 'Dashboard' ? 'Good morning, Prasanna' : activePage} <span>✦</span></h1><p>{pageDescriptions[activePage]}</p></div><span className={`mode-badge ${liveApi ? 'live' : 'demo'}`}>{liveApi ? '● Live API' : '● Demo mode'}</span></div>{apiError && <div role="alert" className="banner warning">{apiError}</div>}{notice && <div role="status" className="banner success"><span>{notice}</span><button onClick={() => setNotice('')}>×</button></div>}{activePage === 'Dashboard' && renderDashboard()}{activePage === 'Jobs' && renderJobs()}{(activePage === 'Candidates' || activePage === 'Shortlisted') && renderCandidates()}{activePage === 'Settings' && renderSettings()}<footer><span>TalentFlow AI · Resume screening pipeline</span><span>{liveApi ? 'Connected to live API' : 'Frontend demo ready'} <i /></span></footer></div></main>
    </div>
  )
}

export default App
