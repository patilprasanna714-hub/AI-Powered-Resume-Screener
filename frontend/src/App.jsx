import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  getCandidates,
  getJobs,
  getShortlistCsvUrl,
  updateCandidateDecision,
} from './api'

const API_URL = import.meta.env.VITE_API_BASE_URL

const initialCandidates = [
  {
    id: 1,
    parsed_profile: {
      name: 'SARAH JENKINS',
      email: 'sarah.jenkins@example.com',
      phone: '+1 (555) 234-5678',
      skills: ['API Gateway', 'AWS', 'Bash', 'CI/CD', 'CloudWatch', 'Docker', 'DynamoDB', 'Git', 'GitHub Actions', 'Jenkins', 'Lambda', 'Linux', 'Microservices', 'Python', 'REST APIs', 'S3', 'SQL', 'SQS', 'Serverless', 'Terraform'],
      job_titles: ['Cloud Engineer', 'Cloud Support Associate', 'Systems Engineer'],
      experience_years: 9.7,
      links: { linkedin: 'https://linkedin.com/in/sarahjenkins-cloud', github: 'https://github.com/sjenkins-dev' },
      confidence: { overall: 0.95 },
      flags: [],
    },
    status: 'New',
  },
]

const sampleJobs = [
  { id: 'sample-design', title: 'Senior Product Designer', department: 'Design', location: 'New York · Hybrid', applicants: 48, progress: 76, color: 'purple' },
  { id: 'sample-frontend', title: 'Frontend Engineer', department: 'Engineering', location: 'Remote · US', applicants: 36, progress: 58, color: 'cyan' },
  { id: 'sample-product', title: 'Product Manager', department: 'Product', location: 'San Francisco · Hybrid', applicants: 24, progress: 42, color: 'orange' },
]

const currentDate = new Intl.DateTimeFormat('en-US', {
  weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
}).format(new Date()).toUpperCase()

function Icon({ name, size = 18 }) {
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    briefcase: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18M10 12v2h4v-2" /></>,
    users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>,
    star: <path d="m12 3 2.78 5.63 6.22.9-4.5 4.38 1.06 6.19L12 17.18l-5.56 2.92 1.06-6.19L3 9.53l6.22-.9L12 3Z" />,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-1.41 1.41-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V20h-2v-.09A1.7 1.7 0 0 0 12.39 18.4a1.7 1.7 0 0 0-1.88.34l-.06.06-1.41-1.41.06-.06A1.7 1.7 0 0 0 9.44 15a1.7 1.7 0 0 0-1.56-1.03H7v-2h.88A1.7 1.7 0 0 0 9.44 11a1.7 1.7 0 0 0-.34-1.88l-.06-.06 1.41-1.41.06.06a1.7 1.7 0 0 0 1.88.34A1.7 1.7 0 0 0 13.42 6.5V6h2v.5A1.7 1.7 0 0 0 16.45 8a1.7 1.7 0 0 0 1.88-.34l.06-.06 1.41 1.41-.06.06A1.7 1.7 0 0 0 19.4 11a1.7 1.7 0 0 0 1.56 1.03H22v2h-.?" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></>,
    upload: <><path d="M12 16V4M7 9l5-5 5 5M4 20h16" /></>,
    download: <><path d="M12 4v12M7 11l5 5 5-5M4 20h16" /></>,
    chevron: <path d="m6 9 6 6 6-6" />,
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
  if (Array.isArray(data?.data?.items)) return data.data.items
  return []
}

function candidateId(candidate) {
  return candidate.id ?? candidate.candidate_id ?? candidate.candidateId
}

function App() {
  const [candidates, setCandidates] = useState(initialCandidates)
  const [apiJobs, setApiJobs] = useState([])
  const [selectedJobId, setSelectedJobId] = useState(null)
  const [apiLoading, setApiLoading] = useState(Boolean(API_URL))
  const [apiError, setApiError] = useState('')
  const [pendingId, setPendingId] = useState(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('All statuses')
  const [activePage, setActivePage] = useState('Dashboard')
  const [uploadedFile, setUploadedFile] = useState('')
  const fileInputRef = useRef(null)

  useEffect(() => {
    if (!API_URL) return undefined
    let cancelled = false
    async function loadJobs() {
      try {
        console.log('[API] Resolved API base URL:', API_URL)
        setApiError('')
        const response = await getJobs()
        const payload = await response.json()
        console.log('[API] /jobs response:', payload)
        const list = listFromResponse(payload, 'jobs')
        if (!cancelled) {
          setApiJobs(list)
          setSelectedJobId(list[0]?.job_id ?? list[0]?.jobId ?? list[0]?.id ?? null)
        }
      } catch (error) {
        console.error('[API] /jobs error:', error)
        if (!cancelled) setApiError(error.message || 'Unable to load jobs.')
      } finally {
        if (!cancelled) setApiLoading(false)
      }
    }
    loadJobs()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!API_URL || selectedJobId == null) return undefined
    let cancelled = false
    async function loadCandidates() {
      setApiLoading(true)
      try {
        const response = await getCandidates(selectedJobId)
        const payload = await response.json()
        console.log('[API] candidates response:', payload)
        const list = listFromResponse(payload, 'candidates')
        if (!cancelled) setCandidates(list)
      } catch (error) {
        console.error('[API] candidates error:', error)
        if (!cancelled) setApiError(error.message || 'Unable to load candidates.')
      } finally {
        if (!cancelled) setApiLoading(false)
      }
    }
    loadCandidates()
    return () => { cancelled = true }
  }, [selectedJobId])

  const jobs = API_URL && apiJobs.length ? apiJobs : sampleJobs
  const filteredCandidates = useMemo(() => candidates.filter((candidate) => {
    const profile = candidate.parsed_profile || {}
    const searchText = [profile.name, ...(profile.job_titles || []), ...(profile.skills || []), profile.email, profile.phone].filter(Boolean).join(' ').toLowerCase()
    return searchText.includes(search.toLowerCase()) && (statusFilter === 'All statuses' || candidate.status === statusFilter)
  }), [candidates, search, statusFilter])

  const updateStatus = async (id, status) => {
    if (API_URL && selectedJobId != null) {
      try {
        setPendingId(id)
        setApiError('')
        await updateCandidateDecision(selectedJobId, id, status === 'Shortlisted' ? 'SHORTLIST' : 'REJECT')
        setCandidates((current) => current.map((candidate) => candidateId(candidate) === id ? { ...candidate, status } : candidate))
      } catch (error) {
        setApiError(error.message || 'Unable to update candidate.')
      } finally {
        setPendingId(null)
      }
      return
    }
    setCandidates((current) => current.map((candidate) => candidateId(candidate) === id ? { ...candidate, status } : candidate))
  }

  const downloadCsv = () => {
    if (API_URL && selectedJobId != null) {
      const link = document.createElement('a')
      link.href = getShortlistCsvUrl(selectedJobId)
      link.download = 'talentflow-shortlist.csv'
      link.click()
      return
    }
    const rows = [['Candidate', 'Job Titles', 'Experience', 'Skills', 'Email', 'Phone', 'Confidence', 'Flags', 'Status'], ...candidates.map((candidate) => {
      const profile = candidate.parsed_profile || {}
      return [profile.name || '', (profile.job_titles || []).join(' / '), profile.experience_years != null ? `${profile.experience_years} yrs` : '', (profile.skills || []).join(' / '), profile.email || '', profile.phone || '', profile.confidence?.overall != null ? `${Math.round(profile.confidence.overall * 100)}%` : '', (profile.flags || []).join(' / '), candidate.status || '']
    })]
    const csv = rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(',')).join('\n')
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    link.download = 'talentflow-shortlist.csv'
    link.click()
    URL.revokeObjectURL(link.href)
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">✦</span><span>talent<span className="brand-accent">flow</span></span></div>
        <div className="workspace-label">WORKSPACE</div>
        <nav className="main-nav" aria-label="Main navigation">
          {[['Dashboard', 'grid'], ['Jobs', 'briefcase'], ['Candidates', 'users'], ['Shortlisted', 'star']].map(([label, icon]) => <button key={label} className={activePage === label ? 'nav-item active' : 'nav-item'} onClick={() => setActivePage(label)}><Icon name={icon} /><span>{label}</span></button>)}
        </nav>
        <div className="sidebar-bottom"><button className={activePage === 'Settings' ? 'nav-item active' : 'nav-item'} onClick={() => setActivePage('Settings')}><Icon name="settings" /><span>Settings</span></button></div>
      </aside>
      <main className="main-content">
        <header className="topbar"><button className="mobile-menu" aria-label="Open menu"><Icon name="menu" /></button><div className="breadcrumb"><span>Workspace</span><b>/</b><strong>{activePage}</strong></div><div className="top-actions"><Icon name="search" /><Icon name="bell" /><strong>PP</strong></div></header>
        <div className="content-wrap">
          <div className="page-heading"><div><div className="eyebrow">{currentDate}</div><h1>Good morning, Prasanna <span>✦</span></h1><p>Here’s what’s happening with your talent pipeline today.</p></div></div>
          {apiError && <div role="alert" className="error-message">{apiError}</div>}
          <section className="summary-grid" aria-label="Recruiting summary">{[['Total candidates', '1,284', '+12.8%', 'vs. last month', 'users', 'cyan'], ['Active jobs', String(jobs.length || 24), '+4.2%', 'vs. last month', 'briefcase', 'purple'], ['Shortlisted', String(candidates.filter((candidate) => candidate.status === 'Shortlisted').length || 86), '+18.5%', 'vs. last month', 'star', 'green']].map(([label, value, change, note, icon, color]) => <div className="summary-card" key={label}><Icon name={icon} /><div><div className="summary-label">{label}</div><strong className="summary-value">{value}</strong><small className={`summary-change ${color}`}>{change} <span>{note}</span></small></div></div>)}</section>
          <div className="section-grid"><section className="panel jobs-panel"><div className="panel-heading"><div><h2>Active jobs</h2><p>Track your open roles and applications</p></div></div>{apiLoading && API_URL ? <p>Loading jobs…</p> : jobs.map((job) => <button className="job-row" key={job.id ?? job.job_id ?? job.title} onClick={() => API_URL && setSelectedJobId(job.job_id ?? job.jobId ?? job.id)}><span className={`job-icon ${job.color || 'purple'}`}><Icon name="briefcase" /></span><span><strong>{job.title || job.name || 'Untitled job'}</strong><small>{job.department || job.location || 'Open position'}</small></span><b>{job.applicants ?? 0}</b></button>)}</section>
          <section className="panel candidates-panel"><div className="panel-heading candidate-heading"><div><h2>Candidate pipeline</h2><p>Review and manage your latest applicants</p></div><button className="outline-button" onClick={downloadCsv}><Icon name="download" /> Download shortlist</button></div><div className="filters"><label><Icon name="search" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search candidates" /></label><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option>All statuses</option><option>New</option><option>Shortlisted</option><option>Rejected</option></select></div><div className="table-wrap"><table><thead><tr><th>Candidate</th><th>Role</th><th>Experience</th><th>Skills</th><th>Score</th><th>Status</th><th>Actions</th></tr></thead><tbody>{apiLoading && API_URL && selectedJobId != null ? <tr><td colSpan="7">Loading candidates…</td></tr> : filteredCandidates.map((candidate) => { const profile = candidate.parsed_profile || {}; const confidence = profile.confidence?.overall != null ? Math.round(profile.confidence.overall * 100) : null; const id = candidateId(candidate); return <tr key={id}><td><div className="candidate-name"><div className="avatar cyan">{(profile.name || 'NA').split(' ').map((part) => part[0]).join('').slice(0, 2)}</div><div><strong>{profile.name || 'Unknown Candidate'}</strong><small>{profile.email || 'No email'}</small><small>{profile.phone || 'No phone'}</small></div></div></td><td>{(profile.job_titles || []).length ? profile.job_titles.join(' / ') : 'Not available'}<div>{profile.links?.linkedin && <a href={profile.links.linkedin} target="_blank" rel="noreferrer">LinkedIn</a>}{profile.links?.linkedin && profile.links?.github && ' · '}{profile.links?.github && <a href={profile.links.github} target="_blank" rel="noreferrer">GitHub</a>}</div></td><td>{profile.experience_years != null ? `${profile.experience_years} yrs` : 'N/A'}</td><td><div className="skill-list">{(profile.skills || []).slice(0, 4).map((skill) => <span key={skill}>{skill}</span>)}{(profile.skills || []).length > 4 && <small>+{profile.skills.length - 4}</small>}</div></td><td><div className="score"><div className="score-ring" style={{ '--score': `${(confidence ?? 0) * 3.6}deg` }}><span>{confidence != null ? `${confidence}%` : 'N/A'}</span></div></div></td><td><span className={`status ${(candidate.status || 'New').toLowerCase()}`}>{candidate.status || 'New'}</span></td><td><div className="row-actions"><button className="shortlist-button" onClick={() => updateStatus(id, 'Shortlisted')} disabled={pendingId === id || candidate.status === 'Shortlisted'}>Shortlist</button><button className="reject-button" onClick={() => updateStatus(id, 'Rejected')} disabled={pendingId === id || candidate.status === 'Rejected'}>Reject</button></div></td></tr> })}</tbody></table></div></section></div>
          <footer><span>TalentLens AI · Pipeline overview</span><span>Last synced a few seconds ago <i /></span></footer>
        </div>
      </main>
    </div>
  )
}

export default App
