import { useCallback, useEffect, useMemo, useState } from 'react'
import EmployeeDashboard from './EmployeeDashboard.jsx'

const API_OPTIONS = { credentials: 'include' }

class SessionExpiredError extends Error {
  constructor() {
    super('Your session has expired. Please sign in again.')
    this.name = 'SessionExpiredError'
  }
}

async function readError(response, fallback) {
  try {
    const data = await response.json()
    if (Array.isArray(data.detail)) {
      const messages = data.detail
        .map((item) => {
          if (typeof item === 'string') return item
          const field = Array.isArray(item.loc) ? item.loc.at(-1) : ''
          const message = item.msg || 'Invalid value'
          return field && field !== 'body' ? `${String(field).replaceAll('_', ' ')}: ${message}` : message
        })
        .filter(Boolean)
      return messages.join(' · ') || fallback
    }
    return data.detail || data.message || fallback
  } catch {
    return fallback
  }
}

function BrandMark({ compact = false }) {
  return (
    <div className={`brand ${compact ? 'brand--compact' : ''}`} aria-label="SkillBridge">
      <span className="brand__mark" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span className="brand__name">SkillBridge</span>
    </div>
  )
}

function Spinner({ label = 'Loading' }) {
  return (
    <span className="spinner-wrap" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </span>
  )
}

function Alert({ children, variant = 'error' }) {
  return (
    <div className={`alert alert--${variant}`} role={variant === 'error' ? 'alert' : 'status'}>
      <span className="alert__icon" aria-hidden="true">
        {variant === 'error' ? '!' : 'i'}
      </span>
      <span>{children}</span>
    </div>
  )
}

function LoginScreen({ onLogin, notice }) {
  const [mode, setMode] = useState('login')
  const [fullName, setFullName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(event) {
    event.preventDefault()
    if ((mode === 'register' && !fullName.trim()) || !username.trim() || !password) {
      setError(mode === 'register' ? 'Enter your full name, username, and password.' : 'Enter both your username and password.')
      return
    }
    if (mode === 'register' && password.length < 8) {
      setError('Choose a password with at least 8 characters.')
      return
    }

    setSubmitting(true)
    setError('')
    try {
      const response = await fetch(mode === 'register' ? '/api/auth/register' : '/api/auth/login', {
        ...API_OPTIONS,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mode === 'register'
          ? { full_name: fullName.trim(), username: username.trim(), password }
          : { username: username.trim(), password }),
      })

      if (!response.ok) {
        throw new Error(await readError(
          response,
          mode === 'register' ? 'Account creation failed. Please try again.' : 'Sign in failed. Please try again.',
        ))
      }

      const data = await response.json()
      setPassword('')
      onLogin(data)
    } catch (requestError) {
      setError(requestError.message || 'Unable to reach the server. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  function switchMode(nextMode) {
    if (submitting || nextMode === mode) return
    setMode(nextMode)
    setError('')
    setPassword('')
  }

  return (
    <main className="login-shell">
      <section className="login-story" aria-labelledby="story-title">
        <BrandMark />
        <div className="login-story__copy">
          <p className="eyebrow eyebrow--light">Workforce intelligence, simplified</p>
          <h1 id="story-title">Turn skill gaps into a clear growth plan.</h1>
          <p>
            Compare employee capabilities with role expectations and find the most relevant
            learning path in seconds.
          </p>
        </div>
        <div className="story-preview" aria-hidden="true">
          <div className="story-preview__head">
            <span>Team readiness</span>
            <span className="story-preview__badge">LIVE VIEW</span>
          </div>
          <div className="story-preview__score">
            <strong>74%</strong>
            <span>role-ready skills</span>
          </div>
          <div className="story-preview__bars">
            <i style={{ '--bar': '92%' }} />
            <i style={{ '--bar': '72%' }} />
            <i style={{ '--bar': '84%' }} />
            <i style={{ '--bar': '56%' }} />
            <i style={{ '--bar': '68%' }} />
          </div>
        </div>
        <p className="login-story__foot">Private career &amp; HR workspace · Local project data</p>
      </section>

      <section className="login-panel" aria-labelledby="login-title">
        <div className="login-card">
          <div className="login-mobile-brand">
            <BrandMark compact />
          </div>
          <p className="eyebrow">Secure workspace</p>
          <h2 id="login-title">{mode === 'login' ? 'Welcome back' : 'Create your profile'}</h2>
          <p className="login-card__intro">
            {mode === 'login'
              ? 'Sign in as an administrator or employee.'
              : 'Employees can register, build a profile, and explore target roles.'}
          </p>

          <div className="auth-tabs" role="tablist" aria-label="Account access">
            <button
              aria-selected={mode === 'login'}
              className={mode === 'login' ? 'auth-tab auth-tab--active' : 'auth-tab'}
              onClick={() => switchMode('login')}
              role="tab"
              type="button"
            >
              Sign in
            </button>
            <button
              aria-selected={mode === 'register'}
              className={mode === 'register' ? 'auth-tab auth-tab--active' : 'auth-tab'}
              onClick={() => switchMode('register')}
              role="tab"
              type="button"
            >
              Employee registration
            </button>
          </div>

          {notice && <Alert variant="info">{notice}</Alert>}
          {error && <Alert>{error}</Alert>}

          <form onSubmit={handleSubmit} className="login-form">
            {mode === 'register' && (
              <label>
                <span>Full name</span>
                <span className="input-wrap">
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm7 8a7 7 0 0 0-14 0" />
                  </svg>
                  <input
                    autoComplete="name"
                    autoFocus
                    disabled={submitting}
                    onChange={(event) => setFullName(event.target.value)}
                    placeholder="Enter your full name"
                    type="text"
                    value={fullName}
                  />
                </span>
              </label>
            )}
            <label>
              <span>Username</span>
              <span className="input-wrap">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm7 8a7 7 0 0 0-14 0" />
                </svg>
                <input
                  autoComplete="username"
                  autoFocus={mode === 'login'}
                  disabled={submitting}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="Enter your username"
                  type="text"
                  value={username}
                />
              </span>
            </label>

            <label>
              <span>Password</span>
              <span className="input-wrap">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <rect x="5" y="10" width="14" height="10" rx="2" />
                  <path d="M8 10V7a4 4 0 0 1 8 0v3" />
                </svg>
                <input
                  autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                  disabled={submitting}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Enter your password"
                  type="password"
                  value={password}
                />
              </span>
            </label>

            <button className="button button--primary button--login" disabled={submitting} type="submit">
              {submitting
                ? <Spinner label={mode === 'register' ? 'Creating account…' : 'Signing in…'} />
                : mode === 'register' ? 'Create employee account' : 'Sign in securely'}
            </button>
          </form>
          <p className="login-card__note">
            {mode === 'login'
              ? 'Administrators use the account created during setup.'
              : 'Registration creates an employee account only.'}
          </p>
        </div>
      </section>
    </main>
  )
}

function AppHeader({ user, username, onLogout, loggingOut }) {
  const displayName = user?.full_name || user?.username || username || 'Administrator'
  const initials = displayName
    .split(/\s+/)
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
  const roleLabel = user?.role === 'employee' ? 'Employee workspace' : 'HR administrator'

  return (
    <header className="app-header">
      <div className="app-header__inner">
        <BrandMark compact />
        <div className="app-header__actions">
          <div className="admin-identity">
            <span className="admin-identity__avatar" aria-hidden="true">{initials}</span>
            <span className="admin-identity__copy">
              <strong>{displayName}</strong>
              <small>{roleLabel}</small>
            </span>
          </div>
          <span className="header-divider" aria-hidden="true" />
          <button
            aria-label={loggingOut ? 'Signing out…' : 'Sign out'}
            className="logout-button"
            disabled={loggingOut}
            onClick={onLogout}
            type="button"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4M14 8l4 4-4 4M18 12H9" />
            </svg>
            <span>{loggingOut ? 'Signing out…' : 'Sign out'}</span>
          </button>
        </div>
      </div>
    </header>
  )
}

function SelectField({ id, label, value, onChange, options, placeholder, optionLabel, disabled = false }) {
  return (
    <label className="select-field" htmlFor={id}>
      <span>{label}</span>
      <span className="select-wrap">
        <select disabled={disabled} id={id} onChange={(event) => onChange(event.target.value)} value={value}>
          <option value="">{placeholder}</option>
          {options.map((option) => (
            <option key={option.id} value={option.id}>{optionLabel(option)}</option>
          ))}
        </select>
      </span>
    </label>
  )
}

function EmptyAnalysis() {
  return (
    <section className="empty-state" aria-labelledby="empty-title">
      <div className="empty-state__visual" aria-hidden="true">
        <span className="orbit orbit--one" />
        <span className="orbit orbit--two" />
        <span className="empty-state__icon">
          <svg viewBox="0 0 48 48">
            <path d="M9 38V24M19 38V14M29 38V20M39 38V8" />
            <path d="m7 17 11-7 10 4L40 5" />
            <circle cx="7" cy="17" r="2" />
            <circle cx="18" cy="10" r="2" />
            <circle cx="28" cy="14" r="2" />
            <circle cx="40" cy="5" r="2" />
          </svg>
        </span>
      </div>
      <p className="eyebrow">Ready when you are</p>
      <h2 id="empty-title">Build a focused learning plan</h2>
      <p>Select an employee and a target role above, then run the analysis to see strengths, gaps, and recommended courses.</p>
      <div className="empty-state__steps" aria-label="Three analysis steps">
        <span><b>1</b> Select employee</span>
        <i aria-hidden="true" />
        <span><b>2</b> Choose role</span>
        <i aria-hidden="true" />
        <span><b>3</b> Review plan</span>
      </div>
    </section>
  )
}

function AnalysisLoading() {
  return (
    <section className="analysis-loading" aria-live="polite" aria-busy="true">
      <div className="loading-orb"><span /></div>
      <h2>Comparing skill profiles…</h2>
      <p>Checking role expectations and matching relevant training.</p>
      <div className="skeleton-grid" aria-hidden="true">
        <span /><span /><span />
      </div>
    </section>
  )
}

function SummaryCard({ tone, label, value, detail, icon }) {
  return (
    <article className={`summary-card summary-card--${tone}`}>
      <span className="summary-card__icon" aria-hidden="true">{icon}</span>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </article>
  )
}

function SkillChart({ skills }) {
  return (
    <section className="panel chart-panel" aria-labelledby="chart-title">
      <div className="panel-heading panel-heading--chart">
        <div>
          <p className="section-kicker">Proficiency comparison</p>
          <h2 id="chart-title">Current vs. required level</h2>
        </div>
        <div className="chart-legend" aria-label="Chart legend">
          <span><i className="legend-current" />Current</span>
          <span><i className="legend-required" />Required</span>
        </div>
      </div>

      <div className="skill-chart">
        <div className="chart-axis" aria-hidden="true">
          <span />
          <div>{[0, 1, 2, 3, 4, 5].map((level) => <b key={level}>{level}</b>)}</div>
        </div>
        {skills.map((skill) => {
          const ratingLabel = skill.rating_source || (skill.assessed ? '' : 'Unassessed')
          const evidenceLabel = skill.mentioned ? 'Résumé mention' : ''
          const meta = [
            skill.assessed ? `Level ${skill.current_label ?? skill.current_level}` : (skill.current_label || 'Unassessed'),
            ratingLabel === 'Self-reported' ? ratingLabel : evidenceLabel,
          ].filter(Boolean).join(' · ')

          return <div className="chart-row" key={skill.skill}>
            <div className="chart-row__label">
              <strong>{skill.skill}</strong>
              <small className={!skill.assessed ? 'text-warning' : ''}>
                {meta}
              </small>
            </div>
            <div
              className="chart-track"
              role="img"
              aria-label={`${skill.skill}: current ${skill.assessed ? skill.current_level : skill.current_label || 'unassessed'}, required ${skill.required_level ?? 'not rated'}`}
            >
              <span className="chart-gridlines" aria-hidden="true" />
              <span
                className={`chart-current ${skill.required_level !== null && skill.required_level !== undefined && skill.gap === 0 ? 'chart-current--met' : ''}`}
                style={{ width: `${skill.current_level * 20}%` }}
                aria-hidden="true"
              />
              {skill.required_level !== null && skill.required_level !== undefined && (
                <span
                  className="chart-required"
                  style={{ left: `${skill.required_level * 20}%` }}
                  aria-hidden="true"
                >
                  <i>{skill.required_level}</i>
                </span>
              )}
            </div>
          </div>
        })}
      </div>
    </section>
  )
}

function SkillTable({ skills }) {
  const hasProvenance = skills.some((skill) => skill.rating_source || skill.evidence_source || skill.mentioned || skill.confirmed)

  return (
    <section className="panel table-panel" aria-labelledby="table-title">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">Detailed breakdown</p>
          <h2 id="table-title">Skill gap details</h2>
        </div>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Skill</th>
              <th scope="col">Current</th>
              <th scope="col">Required</th>
              <th scope="col">Gap</th>
              {hasProvenance && <th scope="col">Evidence</th>}
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {skills.map((skill) => (
              <tr key={skill.skill}>
                <th scope="row">{skill.skill}</th>
                <td>
                  {skill.assessed ? (
                    <span className="level-value">{skill.current_level}<small>/5</small></span>
                  ) : (
                    <span className="unassessed-pill">{skill.current_label || 'Unassessed'}</span>
                  )}
                </td>
                <td>
                  {skill.required_level !== null && skill.required_level !== undefined ? (
                    <span className="level-value">
                      {skill.required_level}<small>/5</small>
                      {skill.required_label && <small className="skill-scale-note">{skill.required_label}</small>}
                      {skill.importance_label && <small className="skill-scale-note">{skill.importance_label}</small>}
                    </span>
                  ) : <span className="unassessed-pill">Not rated</span>}
                </td>
                <td>
                  <span className={skill.gap > 0 ? 'gap-value' : 'gap-value gap-value--none'}>
                    {skill.gap !== null && skill.gap !== undefined
                      ? skill.gap > 0 ? `+${skill.gap}` : '—'
                      : '—'}
                  </span>
                </td>
                {hasProvenance && (
                  <td>
                    <span className="provenance-list">
                      {skill.rating_source === 'Self-reported' && (
                        <span className="provenance-badge provenance-badge--rated">Self-reported</span>
                      )}
                      {skill.mentioned && (
                        <span className="provenance-badge provenance-badge--resume">Résumé mention</span>
                      )}
                      {skill.confirmed && !skill.mentioned && (
                        <span className="provenance-badge">Added to profile</span>
                      )}
                      {!skill.confirmed && skill.rating_source !== 'Self-reported' && !skill.mentioned && (
                        <span className="provenance-badge">Not detected</span>
                      )}
                    </span>
                  </td>
                )}
                <td>
                  <span className={`status-pill ${skill.needs_attention || skill.gap > 0 ? 'status-pill--gap' : 'status-pill--met'}`}>
                    <i aria-hidden="true" />{skill.status_label || (skill.gap > 0 ? 'Needs focus' : 'Requirement met')}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function formatDuration(duration) {
  if (duration === null || duration === undefined || duration === '') return 'Self-paced'
  if (typeof duration === 'number') return `${duration} hour${duration === 1 ? '' : 's'}`
  return String(duration)
}

function CourseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m3 7 9-4 9 4-9 4-9-4Z" />
      <path d="M7 9v5c0 1.7 2.2 3 5 3s5-1.3 5-3V9M21 7v6" />
    </svg>
  )
}

function Recommendations({ recommendations, allRequirementsMet }) {
  if (allRequirementsMet) {
    return (
      <section className="success-panel" aria-labelledby="success-title">
        <span className="success-panel__icon" aria-hidden="true">
          <svg viewBox="0 0 48 48"><path d="m14 25 7 7 14-16" /></svg>
        </span>
        <div>
          <p className="section-kicker">Role ready</p>
          <h2 id="success-title">All requirements are met</h2>
          <p>This employee already meets or exceeds every skill level required for the selected role. No training is needed right now.</p>
        </div>
      </section>
    )
  }

  return (
    <section className="recommendations" aria-labelledby="recommendations-title">
      <div className="recommendations__heading">
        <div>
          <p className="section-kicker">Personalized learning path</p>
          <h2 id="recommendations-title">Recommended training</h2>
        </div>
        <p>Courses are prioritized for the employee’s current proficiency.</p>
      </div>

      <div className="recommendation-list">
        {recommendations.map((item) => (
          <article className="recommendation-group" key={item.skill}>
            <div className="recommendation-group__title">
              <div>
                <h3>{item.skill}</h3>
                <p>
                  {item.assessed ? `Current level ${item.current_label ?? item.current_level}` : `Currently ${String(item.current_label || 'unassessed').toLowerCase()}`}
                  <span aria-hidden="true"> · </span>
                  {item.gap !== null && item.gap !== undefined
                    ? `${item.gap}-level gap`
                    : item.reason || 'No comparable numeric level'}
                </p>
                {(item.rating_source || item.mentioned) && (
                  <span className="recommendation-source">
                    {item.rating_source === 'Self-reported' ? 'Self-reported rating' : 'Unassessed'}
                    {item.mentioned ? ' · Mentioned in résumé' : ''}
                  </span>
                )}
              </div>
            </div>

            {item.courses?.length ? (
              <div className="course-grid">
                {item.courses.map((course) => (
                  <div className="course-card" key={course.id || `${item.skill}-${course.title}`}>
                    <span className="course-card__icon"><CourseIcon /></span>
                    <div className="course-card__content">
                      <div className="course-card__meta">
                        <span className={`difficulty difficulty--${String(course.difficulty).toLowerCase()}`}>
                          {course.difficulty}
                        </span>
                        <span className="duration">
                          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M12 8v5l3 2" /></svg>
                          {formatDuration(course.estimated_duration)}
                        </span>
                      </div>
                      <h4>{course.title}</h4>
                      <p>Build practical confidence in {item.skill.toLowerCase()}.</p>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="no-course">
                <span aria-hidden="true">!</span>
                <div>
                  <strong>No matching course in the catalog</strong>
                  <p>Consider mentoring, project-based practice, or adding a new course for this skill.</p>
                </div>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}

function Results({ analysis }) {
  const hasComparableAssessment = analysis.summary.skills_met_percentage !== null
    && analysis.summary.skills_met_percentage !== undefined
  const percentage = Math.round(Number(analysis.summary.skills_met_percentage) || 0)
  const allRequirementsMet = analysis.skill_match
    ? analysis.summary.skills_with_gaps === 0 && analysis.summary.assessed_comparable_skill_count > 0
    : analysis.summary.skills_with_gaps === 0
  const employeeName = analysis.employee?.name || analysis.employee?.full_name || 'Your profile'
  const employeeDetails = [analysis.employee?.title, analysis.employee?.department].filter(Boolean).join(' · ')
  const recommendations = analysis.recommendations || []

  return (
    <div className="results" aria-live="polite">
      <section className="results-intro">
        <div>
          <p className="eyebrow">Analysis complete</p>
          <h2>{employeeName} <span>for {analysis.role.name}</span></h2>
          {employeeDetails && <p>{employeeDetails}</p>}
        </div>
        <span className={`readiness-badge ${allRequirementsMet ? 'readiness-badge--ready' : ''}`}>
          <i aria-hidden="true" />{allRequirementsMet ? 'Role ready' : 'Development plan needed'}
        </span>
      </section>

      {analysis.skill_match && (
        <section className="skill-match-explainer" aria-label="Skill match estimate">
          <div className="skill-match-explainer__score">
            <span>Skill match</span>
            <strong>{analysis.skill_match.score}%</strong>
          </div>
          <div>
            <p>{analysis.skill_match.explanation}</p>
            <p>
              {analysis.skill_match.matched_count} of {analysis.skill_match.role_skill_count} mapped role skills confirmed
              {analysis.skill_match.unconfirmed_count > 0
                ? ` · ${analysis.skill_match.unconfirmed_count} not confirmed`
                : ''}
            </p>
          </div>
        </section>
      )}

      <div className="summary-grid">
        <SummaryCard
          tone="teal"
          icon={
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" /><path d="m8.5 12 2.2 2.2 4.8-5" /></svg>
          }
          label="Skills meeting target"
          value={hasComparableAssessment ? `${percentage}%` : '—'}
          detail={analysis.skill_match
            ? `${analysis.summary.skills_met} of ${analysis.summary.assessed_comparable_skill_count} assessed comparable skill levels`
            : `${analysis.summary.skills_met} of ${analysis.summary.total_required_skills} required skills`}
        />
        <SummaryCard
          tone="coral"
          icon={
            <svg viewBox="0 0 24 24"><path d="M4 19V9M10 19V5M16 19v-7M22 19V3" /></svg>
          }
          label="Skills to develop"
          value={analysis.summary.skills_with_gaps}
          detail={analysis.skill_match
            ? 'skills need confirmation, assessment, or development'
            : analysis.summary.skills_with_gaps === 1 ? 'skill has a proficiency gap' : 'skills have proficiency gaps'}
        />
        <SummaryCard
          tone="blue"
          icon={
            <svg viewBox="0 0 24 24"><path d="m3 7 9-4 9 4-9 4-9-4Z" /><path d="M7 9v5c0 1.7 2.2 3 5 3s5-1.3 5-3V9" /></svg>
          }
          label="Training areas"
          value={recommendations.length}
          detail={analysis.skill_match ? 'from the local course catalog' : 'prioritized by current level'}
        />
      </div>

      <SkillChart skills={analysis.skills} />
      <SkillTable skills={analysis.skills} />
      <Recommendations recommendations={recommendations} allRequirementsMet={allRequirementsMet} />
    </div>
  )
}

function Dashboard({ user, apiFetch, onLogout, loggingOut, logoutError }) {
  const [employees, setEmployees] = useState([])
  const [roles, setRoles] = useState([])
  const [employeeId, setEmployeeId] = useState('')
  const [roleId, setRoleId] = useState('')
  const [analysis, setAnalysis] = useState(null)
  const [loadingOptions, setLoadingOptions] = useState(true)
  const [analyzing, setAnalyzing] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true

    async function loadOptions() {
      try {
        const [employeeResponse, roleResponse] = await Promise.all([
          apiFetch('/api/employees'),
          apiFetch('/api/roles'),
        ])
        if (!employeeResponse.ok || !roleResponse.ok) {
          throw new Error('Could not load employees and roles. Please refresh and try again.')
        }
        const [employeeData, roleData] = await Promise.all([
          employeeResponse.json(),
          roleResponse.json(),
        ])
        if (active) {
          setEmployees(employeeData.employees || [])
          setRoles(roleData.roles || [])
        }
      } catch (requestError) {
        if (active && !(requestError instanceof SessionExpiredError)) {
          setError(requestError.message || 'Could not load dashboard data.')
        }
      } finally {
        if (active) setLoadingOptions(false)
      }
    }

    loadOptions()
    return () => { active = false }
  }, [apiFetch])

  const selectedEmployee = useMemo(
    () => employees.find((employee) => employee.id === employeeId),
    [employeeId, employees],
  )

  function updateEmployee(value) {
    setEmployeeId(value)
    setAnalysis(null)
    setError('')
  }

  function updateRole(value) {
    setRoleId(value)
    setAnalysis(null)
    setError('')
  }

  async function runAnalysis() {
    if (!employeeId || !roleId) return
    setAnalyzing(true)
    setAnalysis(null)
    setError('')
    try {
      const params = new URLSearchParams({ employee_id: employeeId, role_id: roleId })
      const response = await apiFetch(`/api/analysis?${params}`)
      if (!response.ok) {
        throw new Error(await readError(response, 'The analysis could not be completed.'))
      }
      setAnalysis(await response.json())
    } catch (requestError) {
      if (!(requestError instanceof SessionExpiredError)) {
        setError(requestError.message || 'The analysis could not be completed.')
      }
    } finally {
      setAnalyzing(false)
    }
  }

  return (
    <div className="app-shell">
      <AppHeader user={user} onLogout={onLogout} loggingOut={loggingOut} />
      <main className="dashboard">
        {logoutError && <Alert>{logoutError}</Alert>}
        <section className="dashboard-hero" aria-labelledby="dashboard-title">
          <div>
            <p className="eyebrow">Talent development</p>
            <h1 id="dashboard-title">Skill gap analysis</h1>
            <p>Compare employee capabilities with a target role and create a practical training plan.</p>
          </div>
          <div className="dashboard-hero__meta" aria-label="Data status">
            <span><i aria-hidden="true" />Local sample data</span>
            <small>Private &amp; demo-ready</small>
          </div>
        </section>

        <section className="analysis-controls" aria-labelledby="controls-title">
          <div className="analysis-controls__heading">
            <span className="analysis-controls__step" aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M4 19V9M10 19V5M16 19v-7M22 19V3" />
              </svg>
            </span>
            <div>
              <h2 id="controls-title">Choose an employee and role</h2>
              <p>We’ll compare every required skill on the same 0–5 scale.</p>
            </div>
          </div>

          {loadingOptions ? (
            <div className="options-loading"><Spinner label="Loading employees and roles…" /></div>
          ) : (
            <div className="control-grid">
              <SelectField
                id="employee"
                label="Employee"
                disabled={analyzing}
                onChange={updateEmployee}
                optionLabel={(employee) => `${employee.name} — ${employee.department}`}
                options={employees}
                placeholder="Select an employee"
                value={employeeId}
              />
              <SelectField
                id="role"
                label="Target job role"
                disabled={analyzing}
                onChange={updateRole}
                optionLabel={(role) => role.name}
                options={roles}
                placeholder="Select a target role"
                value={roleId}
              />
              <button
                className="button button--analyze"
                disabled={!employeeId || !roleId || analyzing}
                onClick={runAnalysis}
                type="button"
              >
                {analyzing ? <Spinner label="Analyzing…" /> : (
                  <>
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19V9M10 19V5M16 19v-7M22 19V3" /></svg>
                    Analyze skill gap
                  </>
                )}
              </button>
            </div>
          )}

          {selectedEmployee && !roleId && (
            <p className="control-hint">Now choose the role you want to compare with {selectedEmployee.name}.</p>
          )}
        </section>

        {error && <Alert>{error}</Alert>}
        {analyzing ? <AnalysisLoading /> : analysis ? <Results analysis={analysis} /> : <EmptyAnalysis />}
      </main>
      <footer className="app-footer">
        <span>SkillBridge</span>
        <span>Employee Skill Gap Analysis &amp; Training Recommendation System</span>
      </footer>
    </div>
  )
}

export default function App() {
  const [authStatus, setAuthStatus] = useState('checking')
  const [user, setUser] = useState(null)
  const [notice, setNotice] = useState('')
  const [loggingOut, setLoggingOut] = useState(false)
  const [logoutError, setLogoutError] = useState('')

  useEffect(() => {
    let active = true

    async function restoreSession() {
      try {
        const response = await fetch('/api/auth/me', API_OPTIONS)
        if (response.ok) {
          const data = await response.json()
          if (active) {
            setUser(data.user || data)
            setAuthStatus('authenticated')
          }
        } else if (active) {
          setAuthStatus('unauthenticated')
        }
      } catch {
        if (active) {
          setNotice('The API server is unavailable. Start the backend, then try again.')
          setAuthStatus('unauthenticated')
        }
      }
    }

    restoreSession()
    return () => { active = false }
  }, [])

  const expireSession = useCallback(() => {
    setUser(null)
    setLogoutError('')
    setNotice('Your session expired. Please sign in again.')
    setAuthStatus('unauthenticated')
  }, [])

  const apiFetch = useCallback(async (url, options = {}) => {
    const response = await fetch(url, { ...API_OPTIONS, ...options })
    if (response.status === 401) {
      expireSession()
      throw new SessionExpiredError()
    }
    return response
  }, [expireSession])

  function handleLogin(data) {
    setNotice('')
    setLogoutError('')
    setUser(data.user || data)
    setAuthStatus('authenticated')
  }

  async function handleLogout() {
    setLoggingOut(true)
    setLogoutError('')
    try {
      const response = await fetch('/api/auth/logout', { ...API_OPTIONS, method: 'POST' })
      if (!response.ok && response.status !== 401) {
        throw new Error(await readError(response, 'Could not sign out. Please try again.'))
      }

      setUser(null)
      setNotice('You have been signed out.')
      setAuthStatus('unauthenticated')
    } catch (requestError) {
      setLogoutError(requestError.message || 'Could not sign out. Please try again.')
    } finally {
      setLoggingOut(false)
    }
  }

  if (authStatus === 'checking') {
    return (
      <main className="boot-screen">
        <BrandMark />
        <Spinner label="Opening your workspace…" />
      </main>
    )
  }

  if (authStatus !== 'authenticated') {
    return <LoginScreen notice={notice} onLogin={handleLogin} />
  }

  if (user?.role === 'employee') {
    return (
      <EmployeeDashboard
        Alert={Alert}
        AnalysisLoading={AnalysisLoading}
        AppHeader={AppHeader}
        Results={Results}
        SelectField={SelectField}
        Spinner={Spinner}
        apiFetch={apiFetch}
        loggingOut={loggingOut}
        logoutError={logoutError}
        onLogout={handleLogout}
        readError={readError}
        user={user}
      />
    )
  }

  return (
    <Dashboard
      apiFetch={apiFetch}
      loggingOut={loggingOut}
      logoutError={logoutError}
      onLogout={handleLogout}
      user={user}
    />
  )
}
