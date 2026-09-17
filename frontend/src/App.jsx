import { useEffect, useMemo, useRef, useState } from 'react'import './App.css'
import {
  getJobs,
  getCandidates,
  updateCandidateDecision,
  getShortlistCsvUrl
} from './api'
const initialCandidates = [
  {
    id: 1,
    parsed_profile: {
      name: "SARAH JENKINS",
      email: "sarah.jenkins@example.com",
      phone: "+1 (555) 234-5678",
      skills: [
        "API Gateway",
        "AWS",
        "Bash",
        "CI/CD",
        "CloudWatch",
        "Docker",
        "DynamoDB",
        "Git",
        "GitHub Actions",
        "Jenkins",
        "Lambda",
        "Linux",
        "Microservices",
        "Python",
        "REST APIs",
        "S3",
        "SQL",
        "SQS",
        "Serverless",
        "Terraform"
      ],
      job_titles: [
        "Cloud Engineer",
        "Cloud Support Associate",
        "Systems Engineer"
      ],
      experience_years: 9.7,
      links: {
        linkedin: "https://linkedin.com/in/sarahjenkins-cloud",
        github: "https://github.com/sjenkins-dev"
      },
      confidence: {
        overall: 0.95
      },
      flags: []
    },
    status: "New"
  }
]

const jobs = [
  { title: 'Senior Product Designer', department: 'Design', location: 'New York · Hybrid', applicants: 48, progress: 76, color: 'purple' },
  { title: 'Frontend Engineer', department: 'Engineering', location: 'Remote · US', applicants: 36, progress: 58, color: 'cyan' },
  { title: 'Product Manager', department: 'Product', location: 'San Francisco · Hybrid', applicants: 24, progress: 42, color: 'orange' },
]

const currentDate = new Intl.DateTimeFormat('en-US', {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
  year: 'numeric',
}).format(new Date()).toUpperCase()

function Icon({ name, size = 18 }) {
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    briefcase: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18M10 12v2h4v-2" /></>,
    users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></>,
    star: <path d="m12 3 2.78 5.63 6.22.9-4.5 4.38 1.06 6.19L12 17.18l-5.56 2.92 1.06-6.19L3 9.53l6.22-.9L12 3Z" />,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-1.41 1.41-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V20h-2v-.09A1.7 1.7 0 0 0 12.39 18.4a1.7 1.7 0 0 0-1.88.34l-.06.06-1.41-1.41.06-.06A1.7 1.7 0 0 0 9.44 15a1.7 1.7 0 0 0-1.56-1H7v-2h.09A1.7 1.7 0 0 0 8.6 11a1.7 1.7 0 0 0-.34-1.88L8.2 9.06l1.41-1.41.06.06A1.7 1.7 0 0 0 11.56 8a1.7 1.7 0 0 0 1.03-1.56V6h2v.09A1.7 1.7 0 0 0 15.61 7.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 1.41 1.41-.06.06A1.7 1.7 0 0 0 18.56 11a1.7 1.7 0 0 0 1.56 1H20v2h-.09A1.7 1.7 0 0 0 19.4 15Z" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></>,
    upload: <><path d="M12 16V4M7 9l5-5 5 5M4 20h16" /></>,
    download: <><path d="M12 4v12M7 11l5 5 5-5M4 20h16" /></>,
    chevron: <path d="m6 9 6 6 6-6" />,
    menu: <><path d="M4 6h16M4 12h16M4 18h16" /></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function App() {
  const [candidates, setCandidates] = useState(initialCandidates)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('All statuses')
  const [activePage, setActivePage] = useState('Dashboard')
  const [uploadedFile, setUploadedFile] = useState('')
  const fileInputRef = useRef(null)

 const filteredCandidates = useMemo(() => candidates.filter((candidate) => {
  const profile = candidate.parsed_profile || {}

  const searchText = [
    profile.name,
    ...(profile.job_titles || []),
    ...(profile.skills || []),
    profile.email,
    profile.phone
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()

  const searchMatch = searchText.includes(search.toLowerCase())
  const statusMatch =
    statusFilter === 'All statuses' || candidate.status === statusFilter

  return searchMatch && statusMatch
}), [candidates, search, statusFilter])

  const updateStatus = (id, status) => setCandidates((current) => current.map((candidate) => candidate.id === id ? { ...candidate, status } : candidate))

 const downloadCsv = () => {
  const rows = [
    [
      'Candidate',
      'Job Titles',
      'Experience',
      'Skills',
      'Email',
      'Phone',
      'Confidence',
      'Flags',
      'Status'
    ],
    ...candidates.map((candidate) => {
      const profile = candidate.parsed_profile || {}

      return [
        profile.name || '',
        (profile.job_titles || []).join(' / '),
        profile.experience_years != null
          ? `${profile.experience_years} yrs`
          : '',
        (profile.skills || []).join(' / '),
        profile.email || '',
        profile.phone || '',
        profile.confidence?.overall != null
          ? `${Math.round(profile.confidence.overall * 100)}%`
          : '',
        (profile.flags || []).join(' / '),
        candidate.status || ''
      ]
    })
  ]

  const csv = rows
    .map((row) =>
      row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(',')
    )
    .join('\n')

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
          {[['Dashboard', 'grid'], ['Jobs', 'briefcase'], ['Candidates', 'users'], ['Shortlisted', 'star']].map(([label, icon]) => <button className={activePage === label ? 'nav-item active' : 'nav-item'} onClick={() => setActivePage(label)} key={label}><Icon name={icon} /><span>{label}</span>{label === 'Candidates' && <span className="nav-count">12</span>}</button>)}
        </nav>
        <div className="sidebar-bottom"><button className={activePage === 'Settings' ? 'nav-item active' : 'nav-item'} onClick={() => setActivePage('Settings')}><Icon name="settings" /><span>Settings</span></button><div className="help-card"><div className="help-icon">?</div><strong>Need a hand?</strong><span>Visit our help center</span><button>Get support <span>→</span></button></div><div className="sidebar-profile"><div className="avatar profile-avatar">PP</div><div><strong>Prasanna Patil</strong><span>Recruiter</span></div><span className="more">···</span></div></div>
      </aside>

      <main className="main-content">
        <header className="topbar"><button className="mobile-menu" aria-label="Open menu"><Icon name="menu" /></button><div className="breadcrumb"><span>Workspace</span><b>/</b><strong>{activePage}</strong></div><div className="top-actions"><button className="icon-button" aria-label="Notifications"><Icon name="bell" /><i /></button><div className="top-profile"><div className="avatar profile-avatar">PP</div><div><strong>Prasanna Patil</strong><span>Recruiter</span></div><Icon name="chevron" size={14} /></div></div></header>
        <div className="content-wrap">
          <div className="page-heading"><div><div className="eyebrow">{currentDate}</div><h1>Good morning, Prasanna <span>✦</span></h1><p>Here’s what’s happening with your talent pipeline today.</p></div><button className="primary-button" onClick={() => fileInputRef.current?.click()}><Icon name="upload" size={17} /> Upload resume</button></div>

          <section className="summary-grid" aria-label="Recruiting summary">
            {[['Total candidates', '1,284', '+12.8%', 'vs. last month', 'users', 'cyan'], ['Active jobs', '24', '+4.2%', 'vs. last month', 'briefcase', 'purple'], ['Shortlisted', '86', '+18.5%', 'vs. last month', 'star', 'orange'], ['Avg. match score', '82.4%', '+3.1%', 'vs. last month', 'grid', 'green']].map(([label, value, change, detail, icon, color]) => <div className="summary-card" key={label}><div className={`summary-icon ${color}`}><Icon name={icon} size={19} /></div><span className="summary-label">{label}</span><strong className="summary-value">{value}</strong><div className="summary-change"><span>↗ {change}</span> {detail}</div></div>)}
          </section>

          <div className="section-grid"><section className="panel jobs-panel"><div className="panel-heading"><div><h2>Active jobs</h2><p>Track your open roles and applications</p></div><button className="text-button">View all <span>→</span></button></div><div className="job-list">{jobs.map((job) => <div className="job-row" key={job.title}><div className={`job-icon ${job.color}`}><Icon name="briefcase" size={17} /></div><div className="job-info"><strong>{job.title}</strong><span>{job.department} · {job.location}</span></div><div className="job-progress"><div><span>{job.applicants} applicants</span><b>{job.progress}%</b></div><div className="progress-track"><i className={job.color} style={{ width: `${job.progress}%` }} /></div></div></div>)}</div></section><section className="panel upload-panel"><div className="upload-heading"><div className="upload-orb"><Icon name="upload" size={20} /></div><div><h2>Screen a resume</h2><p>Upload a file to get an instant AI match</p></div></div><button className="dropzone" onClick={() => fileInputRef.current?.click()}><Icon name="upload" size={21} /><strong>{uploadedFile || 'Drop a resume here'}</strong><span>{uploadedFile ? 'Ready to screen' : 'PDF or DOCX · max 10MB'}</span></button><input ref={fileInputRef} hidden type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(event) => setUploadedFile(event.target.files?.[0]?.name || '')} /></section></div>

          <section className="panel candidates-panel"><div className="panel-heading candidate-heading"><div><h2>Candidate pipeline</h2><p>Review and manage your latest applicants</p></div><button className="outline-button" onClick={downloadCsv}><Icon name="download" size={16} /> Download shortlist CSV</button></div><div className="table-toolbar"><label className="search-field"><Icon name="search" size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search candidates..." /></label><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filter candidates by status"><option>All statuses</option><option>New</option><option>Reviewing</option><option>Interview</option><option>Shortlisted</option><option>Rejected</option></select><span className="result-count">{filteredCandidates.length} of {candidates.length} candidates</span></div><div className="table-wrap"><table><thead><tr><th>Candidate</th><th>Applied role</th><th>Experience</th><th>Extracted skills</th><th>AI match</th><th>Status</th><th>Actions</th></tr></thead><tbody>{filteredCandidates.map((candidate) => {
  const profile = candidate.parsed_profile || {}
  const confidence = profile.confidence?.overall != null
    ? Math.round(profile.confidence.overall * 100)
    : null

  return (
  <tr key={candidate.id}>
    <td>
      <div className="candidate-name">
        <div className="avatar cyan">
          {(profile.name || 'NA')
            .split(' ')
            .map((part) => part[0])
            .join('')
            .slice(0, 2)}
        </div>
        <div>
          <strong>{profile.name || 'Unknown Candidate'}</strong>
          <small>{profile.email || 'No email'}</small>
          <small>{profile.phone || 'No phone'}</small>
        </div>
      </div>
    </td>

    <td>
      {(profile.job_titles || []).length
        ? profile.job_titles.join(' / ')
        : 'Not available'}
      <div>
        {profile.links?.linkedin && (
          <a href={profile.links.linkedin} target="_blank" rel="noreferrer">
            LinkedIn
          </a>
        )}
        {profile.links?.linkedin && profile.links?.github && ' · '}
        {profile.links?.github && (
          <a href={profile.links.github} target="_blank" rel="noreferrer">
            GitHub
          </a>
        )}
      </div>
    </td>

    <td>
      {profile.experience_years != null
        ? `${profile.experience_years} yrs`
        : 'N/A'}
    </td>

    <td>
      <div className="skill-list">
        {(profile.skills || []).slice(0, 4).map((skill) => (
          <span key={skill}>{skill}</span>
        ))}
        {(profile.skills || []).length > 4 && (
          <small>+{profile.skills.length - 4}</small>
        )}
      </div>

      {(profile.flags || []).length > 0 && (
        <div className="skill-list">
          {profile.flags.map((flag) => (
            <span key={flag}>{flag}</span>
          ))}
        </div>
      )}
    </td>

    <td>
      <div className="score">
        <div
          className="score-ring"
          style={{ '--score': `${(confidence ?? 0) * 3.6}deg` }}
        >
          <span>{confidence != null ? `${confidence}%` : 'N/A'}</span>
        </div>
      </div>
    </td>

    <td>
      <span className={`status ${(candidate.status || 'New').toLowerCase()}`}>
        {candidate.status || 'New'}
      </span>
    </td>

    <td>
      <div className="row-actions">
        <button
          className="shortlist-button"
          onClick={() => updateStatus(candidate.id, 'Shortlisted')}
          disabled={candidate.status === 'Shortlisted'}
        >
          Shortlist
        </button>

        <button
          className="reject-button"
          onClick={() => updateStatus(candidate.id, 'Rejected')}
          disabled={candidate.status === 'Rejected'}
        >
          Reject
        </button>
      </div>
    </td>
  </tr>
)
})}
</tbody>
</table>
</div>
</section>
          <footer><span>TalentLens AI · Pipeline overview</span><span>Last synced a few seconds ago <i /></span></footer>
        </div>
      </main>
    </div>
  )
}

export default App
