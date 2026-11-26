import { useEffect, useMemo, useRef, useState } from 'react'
import { CareerBoundary, normalizeCareers, normalizeJobs } from './CareerSafety.jsx'

const MAX_RESUME_BYTES = 5 * 1024 * 1024
const RESUME_EXTENSIONS = ['.pdf', '.docx']
const JOB_PAGE_SIZE = 10

function blankJobPreferences() {
  return {
    preferred_roles: [],
    location: 'India',
    experience_level: 'any',
    employment_type: 'any',
    career_category: '',
    work_mode: 'any',
    minimum_salary: null,
    updated_at: null,
  }
}

function normalizeJobPreferences(value) {
  const preferences = value || {}
  return {
    preferred_roles: Array.isArray(preferences.preferred_roles)
      ? preferences.preferred_roles.map((role) => String(role).trim()).filter(Boolean)
      : [],
    location: String(preferences.location || ''),
    experience_level: preferences.experience_level || 'any',
    employment_type: preferences.employment_type === 'permanent'
      ? 'full_time'
      : preferences.employment_type || 'any',
    career_category: preferences.career_category || '',
    work_mode: preferences.work_mode || 'any',
    minimum_salary: preferences.minimum_salary === null || preferences.minimum_salary === undefined
      ? null
      : Number(preferences.minimum_salary),
    updated_at: preferences.updated_at || null,
  }
}

function blankProfile(fullName = '') {
  return {
    full_name: fullName,
    email: '',
    phone: '',
    education: '',
    work_experience: '',
    skills: [],
    updated_at: null,
  }
}

function normalizeProfile(profile, fallbackName = '') {
  const value = profile || {}
  return {
    full_name: value.full_name || fallbackName || '',
    email: value.email || '',
    phone: value.phone || '',
    education: value.education || '',
    work_experience: value.work_experience || '',
    skills: Array.isArray(value.skills)
      ? value.skills
        .filter((skill) => skill && String(skill.name || '').trim())
        .map((skill) => ({
          name: String(skill.name).trim(),
          level: skill.level === null || skill.level === undefined || skill.level === ''
            ? null
            : Number(skill.level),
          source: skill.source === 'resume' ? 'resume' : 'manual',
        }))
      : [],
    updated_at: value.updated_at || null,
  }
}

function copyProfile(profile) {
  return { ...profile, skills: profile.skills.map((skill) => ({ ...skill })) }
}

function editablePayload(profile) {
  return {
    full_name: profile.full_name.trim(),
    email: profile.email.trim(),
    phone: profile.phone.trim(),
    education: profile.education.trim(),
    work_experience: profile.work_experience.trim(),
    skills: profile.skills.map(({ name, level, source }) => ({ name: name.trim(), level, source })),
  }
}

function validateResume(file) {
  if (!file) return 'Choose a résumé file to continue.'
  const lowerName = file.name.toLowerCase()
  if (!RESUME_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
    return 'Use a PDF or DOCX file.'
  }
  if (file.size > MAX_RESUME_BYTES) return 'The résumé must be 5 MiB or smaller.'
  return ''
}

function formatUpdated(value) {
  if (!value) return 'Not saved yet'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Saved profile'
  return `Updated ${new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)}`
}

function formatJobSalary(job) {
  if (job.salary_text) return job.salary_text
  const formatter = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  })
  const minimum = Number.isFinite(job.salary_min) ? formatter.format(job.salary_min) : ''
  const maximum = Number.isFinite(job.salary_max) ? formatter.format(job.salary_max) : ''
  if (minimum && maximum) return `${minimum}–${maximum}`
  if (minimum) return `From ${minimum}`
  if (maximum) return `Up to ${maximum}`
  return ''
}

function formatJobDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? ''
    : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

function formatJobType(job) {
  if (job.employment_type_label) return job.employment_type_label
  const isInternship = job.employment_type === 'internship'
    || /\b(intern|internship)\b/i.test(`${job.title || ''} ${job.description || ''}`)
  const contractTime = {
    full_time: 'Full-time',
    part_time: 'Part-time',
  }[job.contract_time] || ''
  const contractType = {
    permanent: 'Permanent',
    contract: 'Contract',
  }[job.employment_type] || ''
  return [
    isInternship ? 'Internship' : '',
    contractTime,
    contractType && !contractTime ? contractType : '',
  ]
    .filter(Boolean)
    .join(' · ')
}

function formatWorkMode(value) {
  return {
    remote: 'Remote',
    hybrid: 'Hybrid',
    on_site: 'On-site',
  }[value] || 'Not specified'
}

async function describeJobSearchError(response, readError) {
  const detail = await readError(response, 'Could not load live job recommendations.')
  if (response.status === 404) {
    return 'The job-search API route was not found. Check that the frontend is proxying to the current backend.'
  }
  if (response.status === 429) {
    return 'A public job provider rate limit was reached. Wait a few minutes and retry.'
  }
  if (response.status >= 500) {
    return `The job provider or backend returned an error (${response.status}). ${detail}`
  }
  return detail
}

function describeJobRequestError(error) {
  if (error?.name === 'TypeError') {
    return 'Could not reach the backend API. Check the frontend proxy target and backend address, then retry.'
  }
  return error?.message || 'Could not load live job recommendations.'
}

function ProfileValue({ value, multiline = false }) {
  if (!value) return <span className="not-detected">Not detected</span>
  return <span className={multiline ? 'profile-value profile-value--multiline' : 'profile-value'}>{value}</span>
}

function ProfileOverview({ profile, onEdit, disabled }) {
  return (
    <div className="profile-overview">
      <div className="profile-overview__actions">
        <div>
          <p className="section-kicker">Saved information</p>
          <h2>Your profile</h2>
        </div>
        <button className="button button--secondary" disabled={disabled} onClick={onEdit} type="button">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 20 4.5-1 10-10a2 2 0 0 0-3-3l-10 10L4 20Z" /><path d="m13.5 7.5 3 3" /></svg>
          Edit profile
        </button>
      </div>

      <dl className="profile-facts">
        <div><dt>Full name</dt><dd><ProfileValue value={profile.full_name} /></dd></div>
        <div><dt>Email</dt><dd><ProfileValue value={profile.email} /></dd></div>
        <div><dt>Phone</dt><dd><ProfileValue value={profile.phone} /></dd></div>
        <div><dt>Education</dt><dd><ProfileValue multiline value={profile.education} /></dd></div>
        <div className="profile-facts__wide"><dt>Work experience</dt><dd><ProfileValue multiline value={profile.work_experience} /></dd></div>
      </dl>

      <div className="saved-skills">
        <h3>Skills</h3>
        {profile.skills.length ? (
          <div className="saved-skill-list">
            {profile.skills.map((skill) => (
              <span className="saved-skill" key={skill.name.toLowerCase()}>
                <b>{skill.name}</b>
                <small>
                  {skill.level === null
                    ? skill.source === 'resume' ? 'Résumé mention · Unassessed' : 'Unassessed'
                    : `Level ${skill.level} · Self-reported`}
                </small>
              </span>
            ))}
          </div>
        ) : <p className="profile-empty-copy">No skills added yet.</p>}
      </div>
    </div>
  )
}

function SkillEditor({ skills, catalog, skillInput, setSkillInput, onAdd, onRemove, onLevel, disabled, error }) {
  function handleKeyDown(event) {
    if (event.key === 'Enter') {
      event.preventDefault()
      onAdd()
    }
  }

  return (
    <section className="skill-editor" aria-labelledby="skills-editor-title">
      <div className="profile-section-heading">
        <div>
          <h3 id="skills-editor-title">Skills &amp; self-rating</h3>
          <p>Add catalog skills or enter your own. A résumé mention is not treated as a rating.</p>
        </div>
        <span>{skills.length} skill{skills.length === 1 ? '' : 's'}</span>
      </div>

      <div className="skill-add-row">
        <label>
          <span className="sr-only">Add a skill</span>
          <input
            disabled={disabled}
            list="canonical-skills"
            onChange={(event) => setSkillInput(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search or type a skill"
            type="text"
            value={skillInput}
          />
          <datalist id="canonical-skills">
            {catalog.map((skill) => <option key={skill} value={skill} />)}
          </datalist>
        </label>
        <button className="button button--add-skill" disabled={disabled || !skillInput.trim()} onClick={onAdd} type="button">
          <span aria-hidden="true">+</span> Add skill
        </button>
      </div>
      {error && <p className="field-error" role="alert">{error}</p>}

      {skills.length ? (
        <div className="editable-skill-list">
          {skills.map((skill, index) => {
            const rated = skill.level !== null
            return (
              <div className="editable-skill" key={`${skill.name.toLowerCase()}-${index}`}>
                <div className="editable-skill__identity">
                  <span className={skill.source === 'resume' ? 'skill-origin skill-origin--resume' : 'skill-origin'} aria-hidden="true">
                    {skill.source === 'resume' ? 'R' : 'M'}
                  </span>
                  <div>
                    <strong>{skill.name}</strong>
                    <small className={rated ? 'skill-rating-label skill-rating-label--rated' : 'skill-rating-label'}>
                      {rated
                        ? 'Self-reported'
                        : skill.source === 'resume' ? 'Mentioned in résumé · Unassessed' : 'Unassessed'}
                    </small>
                  </div>
                </div>
                <label className="rating-select">
                  <span className="sr-only">Rating for {skill.name}</span>
                  <select
                    aria-label={`Rating for ${skill.name}`}
                    disabled={disabled}
                    onChange={(event) => onLevel(index, event.target.value)}
                    value={skill.level ?? ''}
                  >
                    <option value="">Unassessed</option>
                    {[0, 1, 2, 3, 4, 5].map((level) => (
                      <option key={level} value={level}>Level {level}</option>
                    ))}
                  </select>
                </label>
                <button
                  aria-label={`Remove ${skill.name}`}
                  className="remove-skill"
                  disabled={disabled}
                  onClick={() => onRemove(index)}
                  type="button"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M9 7V4h6v3M8 10v8M12 10v8M16 10v8M7 7l1 14h8l1-14" /></svg>
                </button>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="skills-empty">
          <span aria-hidden="true">+</span>
          <p>Add at least one skill to make your role analysis useful.</p>
        </div>
      )}
    </section>
  )
}

function ProfileEditor({
  profile,
  setField,
  catalog,
  skillInput,
  setSkillInput,
  addSkill,
  removeSkill,
  setSkillLevel,
  skillError,
  onSave,
  onCancel,
  busy,
  saving,
  reviewMode,
}) {
  return (
    <form className="profile-editor" onSubmit={onSave}>
      <div className="profile-editor__heading">
        <div>
          <p className="section-kicker">{reviewMode ? 'Résumé review' : 'Profile editor'}</p>
          <h2>{reviewMode ? 'Review before saving' : 'Update your profile'}</h2>
          <p>{reviewMode ? 'Check every extracted field. Missing information is marked for you to complete.' : 'Keep your details and skill ratings current.'}</p>
        </div>
        <span className="draft-badge"><i aria-hidden="true" />Unsaved draft</span>
      </div>

      <div className="profile-field-grid">
        <label>
          <span>Full name <b aria-label="required">*</b></span>
          <input
            autoComplete="name"
            disabled={busy}
            onChange={(event) => setField('full_name', event.target.value)}
            placeholder="Not detected"
            required
            type="text"
            value={profile.full_name}
          />
          {!profile.full_name && <small className="missing-field">Not detected — enter your name</small>}
        </label>
        <label>
          <span>Email</span>
          <input
            autoComplete="email"
            disabled={busy}
            onChange={(event) => setField('email', event.target.value)}
            placeholder="Not detected"
            type="email"
            value={profile.email}
          />
          {!profile.email && <small className="missing-field">Not detected</small>}
        </label>
        <label>
          <span>Phone</span>
          <input
            autoComplete="tel"
            disabled={busy}
            onChange={(event) => setField('phone', event.target.value)}
            placeholder="Not detected"
            type="tel"
            value={profile.phone}
          />
          {!profile.phone && <small className="missing-field">Not detected</small>}
        </label>
        <label>
          <span>Education</span>
          <textarea
            disabled={busy}
            onChange={(event) => setField('education', event.target.value)}
            placeholder="Not detected"
            rows="3"
            value={profile.education}
          />
          {!profile.education && <small className="missing-field">Not detected</small>}
        </label>
        <label className="profile-field--wide">
          <span>Work experience</span>
          <textarea
            disabled={busy}
            onChange={(event) => setField('work_experience', event.target.value)}
            placeholder="Not detected"
            rows="4"
            value={profile.work_experience}
          />
          {!profile.work_experience && <small className="missing-field">Not detected</small>}
        </label>
      </div>

      <SkillEditor
        catalog={catalog}
        disabled={busy}
        error={skillError}
        onAdd={addSkill}
        onLevel={setSkillLevel}
        onRemove={removeSkill}
        setSkillInput={setSkillInput}
        skillInput={skillInput}
        skills={profile.skills}
      />

      <div className="profile-editor__footer">
        <p>Saving replaces your current profile and clears any older analysis.</p>
        <div>
          <button className="button button--ghost" disabled={busy} onClick={onCancel} type="button">Cancel changes</button>
          <button className="button button--save" disabled={busy || !profile.full_name.trim()} type="submit">
            {saving ? 'Saving profile…' : 'Save profile'}
          </button>
        </div>
      </div>
    </form>
  )
}

export default function EmployeeDashboard({
  Alert,
  AnalysisLoading,
  AppHeader,
  Results,
  SelectField,
  Spinner,
  apiFetch,
  loggingOut,
  logoutError,
  onLogout,
  readError,
  user,
}) {
  const initialProfile = useMemo(() => blankProfile(user?.full_name || ''), [user?.full_name])
  const [baselineProfile, setBaselineProfile] = useState(initialProfile)
  const [draft, setDraft] = useState(initialProfile)
  const [profileSaved, setProfileSaved] = useState(false)
  const [editing, setEditing] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [reviewMode, setReviewMode] = useState(false)
  const [roles, setRoles] = useState([])
  const [skillCatalog, setSkillCatalog] = useState([])
  const [roleId, setRoleId] = useState('')
  const [analysis, setAnalysis] = useState(null)
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [pendingFile, setPendingFile] = useState(null)
  const [warnings, setWarnings] = useState([])
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [skillInput, setSkillInput] = useState('')
  const [skillError, setSkillError] = useState('')
  const [jobPreferences, setJobPreferences] = useState(blankJobPreferences)
  const [jobPreferencesDraft, setJobPreferencesDraft] = useState(blankJobPreferences)
  const [preferredRolesInput, setPreferredRolesInput] = useState('')
  const [jobPreferencesLoaded, setJobPreferencesLoaded] = useState(false)
  const [jobPreferencesSaving, setJobPreferencesSaving] = useState(false)
  const [jobPreferencesError, setJobPreferencesError] = useState('')
  const [jobSearchInput, setJobSearchInput] = useState('')
  const [jobQuery, setJobQuery] = useState('')
  const [careerSearchInput, setCareerSearchInput] = useState('')
  const [careerQuery, setCareerQuery] = useState('')
  const [careerCategory, setCareerCategory] = useState('')
  const [jobSort, setJobSort] = useState('relevance')
  const [jobPage, setJobPage] = useState(1)
  const [jobSearchData, setJobSearchData] = useState(null)
  const [jobLoading, setJobLoading] = useState(false)
  const [jobError, setJobError] = useState('')
  const [jobRefresh, setJobRefresh] = useState(0)
  const [careerRecommendationData, setCareerRecommendationData] = useState(null)
  const [careerRecommendationsLoading, setCareerRecommendationsLoading] = useState(false)
  const [careerRecommendationsError, setCareerRecommendationsError] = useState('')
  const [careerRefresh, setCareerRefresh] = useState(0)
  const fileInput = useRef(null)
  const jobSectionRef = useRef(null)

  const busy = uploading || saving || analyzing
  const jobPreferencesDirty = JSON.stringify(jobPreferencesDraft) !== JSON.stringify(jobPreferences)
    || preferredRolesInput !== jobPreferences.preferred_roles.join(', ')

  useEffect(() => {
    let active = true

    async function loadWorkspace() {
      setLoading(true)
      try {
        const [profileResponse, skillsResponse, rolesResponse, jobPreferencesResponse] = await Promise.all([
          apiFetch('/api/profile'),
          apiFetch('/api/skills'),
          apiFetch('/api/roles'),
          apiFetch('/api/job-preferences'),
        ])

        if (!profileResponse.ok) throw new Error(await readError(profileResponse, 'Could not load your profile.'))
        if (!skillsResponse.ok) throw new Error(await readError(skillsResponse, 'Could not load the skill catalog.'))
        if (!rolesResponse.ok) throw new Error(await readError(rolesResponse, 'Could not load target roles.'))
        if (!jobPreferencesResponse.ok) {
          throw new Error(await readError(jobPreferencesResponse, 'Could not load your job preferences.'))
        }

        const [profileData, skillsData, rolesData, jobPreferencesData] = await Promise.all([
          profileResponse.json(),
          skillsResponse.json(),
          rolesResponse.json(),
          jobPreferencesResponse.json(),
        ])
        if (!active) return

        const loadedProfile = normalizeProfile(profileData.profile, user?.full_name)
        const saved = Boolean(profileData.saved)
        setBaselineProfile(loadedProfile)
        setDraft(copyProfile(loadedProfile))
        setProfileSaved(saved)
        setEditing(!saved)
        setSkillCatalog(Array.isArray(skillsData.skills) ? skillsData.skills : [])
        setRoles(Array.isArray(rolesData.roles) ? rolesData.roles : [])
        const loadedPreferences = normalizeJobPreferences(jobPreferencesData.preferences)
        setJobPreferences(loadedPreferences)
        setJobPreferencesDraft(loadedPreferences)
        setPreferredRolesInput(loadedPreferences.preferred_roles.join(', '))
        setJobPreferencesLoaded(true)
      } catch (requestError) {
        if (active && requestError?.name !== 'SessionExpiredError') {
          setError(requestError.message || 'Could not open your employee workspace.')
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    loadWorkspace()
    return () => { active = false }
  }, [apiFetch, readError, user?.full_name])

  useEffect(() => {
    if (!jobPreferencesLoaded || !profileSaved || dirty) {
      setCareerRecommendationData(null)
      setCareerRecommendationsLoading(false)
      return undefined
    }

    let active = true
    setCareerRecommendationsLoading(true)
    setCareerRecommendationsError('')
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 75000)

    async function loadCareerRecommendations() {
      try {
        const params = new URLSearchParams({ limit: '10' })
        if (careerQuery) params.set('query', careerQuery)
        if (careerCategory) params.set('category', careerCategory)
        const response = await apiFetch(`/api/career-recommendations?${params}`, { signal: controller.signal })
        if (!response.ok) {
          throw new Error(await readError(response, 'Could not load career recommendations.'))
        }
        const data = await response.json()
        if (active) setCareerRecommendationData(normalizeCareers(data))
      } catch (requestError) {
        if (active && requestError?.name !== 'SessionExpiredError') {
          setCareerRecommendationsError(
            controller.signal.aborted ? 'Career search timed out. Please retry.' : requestError?.message || 'Could not load career recommendations.',
          )
        }
      } finally {
        clearTimeout(timeout)
        if (active) setCareerRecommendationsLoading(false)
      }
    }

    loadCareerRecommendations()
    return () => { active = false; clearTimeout(timeout); controller.abort() }
  }, [
    apiFetch,
    careerRefresh,
    careerQuery,
    careerCategory,
    dirty,
    jobPreferences,
    jobPreferencesLoaded,
    jobRefresh,
    profileSaved,
    readError,
  ])

  useEffect(() => {
    if (!jobPreferencesLoaded || !profileSaved || dirty) {
      setJobSearchData(null)
      setJobLoading(false)
      return undefined
    }

    let active = true
    const params = new URLSearchParams({
      page: String(jobPage),
      per_page: String(JOB_PAGE_SIZE),
      sort_by: jobSort,
    })
    if (jobQuery) params.set('query', jobQuery)
    setJobLoading(true)
    setJobError('')
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 45000)

    async function loadLiveJobs() {
      try {
        const response = await apiFetch(`/api/jobs?${params}`, { signal: controller.signal })
        if (!response.ok) {
          throw new Error(await describeJobSearchError(response, readError))
        }
        const data = await response.json()
        if (active) setJobSearchData(normalizeJobs(data))
      } catch (requestError) {
        if (active && requestError?.name !== 'SessionExpiredError') {
          setJobError(controller.signal.aborted ? 'Job search timed out. Please retry.' : describeJobRequestError(requestError))
        }
      } finally {
        clearTimeout(timeout)
        if (active) setJobLoading(false)
      }
    }

    loadLiveJobs()
    return () => { active = false; clearTimeout(timeout); controller.abort() }
  }, [
    apiFetch,
    dirty,
    jobPage,
    jobPreferences,
    jobPreferencesLoaded,
    jobQuery,
    jobRefresh,
    jobSort,
    profileSaved,
    readError,
  ])

  function markChanged(nextDraft) {
    setDraft(nextDraft)
    setDirty(true)
    setNotice('')
    setError('')
  }

  function setField(field, value) {
    markChanged({ ...draft, [field]: value })
  }

  function beginEditing() {
    if (busy) return
    setDraft(copyProfile(baselineProfile))
    setEditing(true)
    setReviewMode(false)
    setDirty(false)
    setWarnings([])
    setNotice('')
    setError('')
  }

  function cancelChanges() {
    if (busy) return
    setDraft(copyProfile(baselineProfile))
    setEditing(!profileSaved)
    setReviewMode(false)
    setDirty(false)
    setPendingFile(null)
    setWarnings([])
    setSkillInput('')
    setSkillError('')
    setNotice(profileSaved ? 'Draft discarded. Your saved profile is unchanged.' : 'Draft cleared.')
    setError('')
    if (fileInput.current) fileInput.current.value = ''
  }

  function addSkill() {
    const name = skillInput.trim()
    if (!name) return
    if (draft.skills.some((skill) => skill.name.toLowerCase() === name.toLowerCase())) {
      setSkillError(`${name} is already in your profile.`)
      return
    }
    const catalogMatch = skillCatalog.find((skill) => skill.toLowerCase() === name.toLowerCase())
    markChanged({
      ...draft,
      skills: [...draft.skills, { name: catalogMatch || name, level: null, source: 'manual' }],
    })
    setSkillInput('')
    setSkillError('')
  }

  function removeSkill(index) {
    markChanged({ ...draft, skills: draft.skills.filter((_, skillIndex) => skillIndex !== index) })
    setSkillError('')
  }

  function setSkillLevel(index, value) {
    const level = value === '' ? null : Number(value)
    markChanged({
      ...draft,
      skills: draft.skills.map((skill, skillIndex) => skillIndex === index ? { ...skill, level } : skill),
    })
  }

  async function parseResume(file) {
    const validationError = validateResume(file)
    if (validationError) {
      setError(validationError)
      return
    }

    setUploading(true)
    setError('')
    setNotice('')
    setWarnings([])
    const formData = new FormData()
    formData.append('file', file)

    try {
      const response = await apiFetch('/api/resume/parse', { method: 'POST', body: formData })
      if (!response.ok) throw new Error(await readError(response, 'Could not parse this résumé.'))
      const data = await response.json()
      const parsedProfile = normalizeProfile(data.profile)
      setDraft(parsedProfile)
      setEditing(true)
      setReviewMode(true)
      setDirty(true)
      setWarnings(Array.isArray(data.warnings) ? data.warnings : [])
      setNotice(`${file.name} was parsed into a draft. Review and save it when ready.`)
      setSkillInput('')
      setSkillError('')
    } catch (requestError) {
      if (requestError?.name !== 'SessionExpiredError') {
        setError(requestError.message || 'Could not parse this résumé.')
      }
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  function chooseResume(file) {
    if (busy) return
    const validationError = validateResume(file)
    if (validationError) {
      setError(validationError)
      if (fileInput.current) fileInput.current.value = ''
      return
    }
    if (dirty) {
      setPendingFile(file)
      setError('')
      return
    }
    parseResume(file)
  }

  function handleDrop(event) {
    event.preventDefault()
    setDragging(false)
    chooseResume(event.dataTransfer.files?.[0])
  }

  async function saveProfile(event) {
    event.preventDefault()
    if (busy || !draft.full_name.trim()) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const response = await apiFetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editablePayload(draft)),
      })
      if (!response.ok) throw new Error(await readError(response, 'Could not save your profile.'))
      const data = await response.json()
      const savedProfile = normalizeProfile(data.profile, user?.full_name)
      setBaselineProfile(savedProfile)
      setDraft(copyProfile(savedProfile))
      setProfileSaved(Boolean(data.saved ?? true))
      setEditing(false)
      setReviewMode(false)
      setDirty(false)
      setWarnings([])
      setAnalysis(null)
      setNotice('Profile saved. You can now run a fresh role analysis.')
      setJobSearchInput('')
      setJobQuery('')
      setJobPage(1)
      setJobRefresh((value) => value + 1)
      setCareerRefresh((value) => value + 1)
    } catch (requestError) {
      if (requestError?.name !== 'SessionExpiredError') {
        setError(requestError.message || 'Could not save your profile.')
      }
    } finally {
      setSaving(false)
    }
  }

  async function saveJobPreferences(event) {
    event.preventDefault()
    if (jobPreferencesSaving || !profileSaved || dirty) return
    const preferredRoles = preferredRolesInput
      .split(/[,\n;]/)
      .map((role) => role.trim())
      .filter(Boolean)
    if (preferredRoles.length > 6) {
      setJobPreferencesError('Choose up to six preferred job roles.')
      return
    }

    setJobPreferencesSaving(true)
    setJobPreferencesError('')
    try {
      const response = await apiFetch('/api/job-preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          preferred_roles: preferredRoles,
          location: jobPreferencesDraft.location.trim(),
          experience_level: jobPreferencesDraft.experience_level,
          employment_type: jobPreferencesDraft.employment_type,
          career_category: jobPreferencesDraft.career_category,
          work_mode: jobPreferencesDraft.work_mode,
          minimum_salary: jobPreferencesDraft.minimum_salary === ''
            ? null
            : jobPreferencesDraft.minimum_salary,
        }),
      })
      if (!response.ok) {
        throw new Error(await readError(response, 'Could not save your job preferences.'))
      }
      const data = await response.json()
      const savedPreferences = normalizeJobPreferences(data.preferences)
      setJobPreferences(savedPreferences)
      setJobPreferencesDraft(savedPreferences)
      setPreferredRolesInput(savedPreferences.preferred_roles.join(', '))
      setJobSearchInput('')
      setJobQuery('')
      setJobPage(1)
      setJobSearchData(null)
      setJobRefresh((value) => value + 1)
      setCareerRefresh((value) => value + 1)
    } catch (requestError) {
      if (requestError?.name !== 'SessionExpiredError') {
        setJobPreferencesError(requestError.message || 'Could not save your job preferences.')
      }
    } finally {
      setJobPreferencesSaving(false)
    }
  }

  function searchLiveJobs(event) {
    event.preventDefault()
    if (jobLoading) return
    if (jobQuery === jobSearchInput.trim() && jobPage === 1) setJobRefresh(value => value + 1)
    setJobQuery(jobSearchInput.trim())
    setJobPage(1)
  }

  function searchCareerRoles(event) {
    event.preventDefault()
    if (careerRecommendationsLoading) return
    setCareerQuery(careerSearchInput.trim())
  }

  function searchAlternativeRole(role) {
    setJobSearchInput(role)
    setJobQuery(role)
    setJobPage(1)
  }

  function viewAvailableJobs(role) {
    setJobSearchInput(role.title)
    setJobQuery(role.title)
    setJobPage(1)
    requestAnimationFrame(() => {
      jobSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  function updateRole(value) {
    if (analyzing) return
    setRoleId(value)
    setAnalysis(null)
    setError('')
  }

  async function runAnalysis() {
    if (!profileSaved || dirty || !roleId || analyzing) return
    setAnalyzing(true)
    setError('')
    setNotice('')
    try {
      const params = new URLSearchParams({ role_id: roleId })
      const response = await apiFetch(`/api/my-analysis?${params}`)
      if (!response.ok) throw new Error(await readError(response, 'Could not analyze this role.'))
      setAnalysis(await response.json())
    } catch (requestError) {
      if (requestError?.name !== 'SessionExpiredError') {
        setError(requestError.message || 'Could not analyze this role.')
      }
    } finally {
      setAnalyzing(false)
    }
  }

  return (
    <div className="app-shell employee-shell">
      <AppHeader user={user} onLogout={onLogout} loggingOut={loggingOut} />
      <main className="dashboard employee-dashboard">
        {logoutError && <Alert>{logoutError}</Alert>}
        <section className="dashboard-hero employee-hero" aria-labelledby="employee-dashboard-title">
          <div>
            <p className="eyebrow">My career workspace</p>
            <h1 id="employee-dashboard-title">Build your next-step plan</h1>
            <p>Keep your profile current, compare it with a target role, and focus your learning where it matters.</p>
          </div>
          <div className={`profile-state ${dirty ? 'profile-state--draft' : profileSaved ? 'profile-state--saved' : ''}`}>
            <span><i aria-hidden="true" />{dirty ? 'Unsaved draft' : profileSaved ? 'Profile saved' : 'Profile not saved'}</span>
            <small>{dirty ? 'Analysis still uses your saved profile' : formatUpdated(baselineProfile.updated_at)}</small>
          </div>
        </section>

        {loading ? (
          <section className="employee-loading"><Spinner label="Opening your profile…" /></section>
        ) : (
          <>
            {error && <Alert>{error}</Alert>}
            {notice && <Alert variant="info">{notice}</Alert>}

            <section className="employee-workspace" aria-label="Profile setup">
              <aside className="resume-card">
                <div className="resume-card__heading">
                  <span className="resume-card__icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7V3Z" /><path d="M14 3v5h5M10 13h5M10 17h5" /></svg>
                  </span>
                  <div>
                    <p className="section-kicker">Optional shortcut</p>
                    <h2>Import a résumé</h2>
                  </div>
                </div>
                <p>Extract profile details and mentioned skills into a reviewable draft. Nothing is saved automatically.</p>

                <div
                  className={`resume-dropzone ${dragging ? 'resume-dropzone--active' : ''} ${uploading ? 'resume-dropzone--busy' : ''}`}
                  onDragEnter={(event) => { event.preventDefault(); if (!busy) setDragging(true) }}
                  onDragLeave={() => setDragging(false)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={handleDrop}
                >
                  <input
                    accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    aria-label="Choose résumé file"
                    className="resume-file-input"
                    disabled={busy}
                    onChange={(event) => chooseResume(event.target.files?.[0])}
                    ref={fileInput}
                    type="file"
                  />
                  <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 22V7M10 13l6-6 6 6M6 21v5h20v-5" /></svg>
                  <strong>{uploading ? 'Reading your résumé…' : 'Drop a file here'}</strong>
                  <span>or choose a file</span>
                  <small>PDF or DOCX · maximum 5 MiB</small>
                </div>

                <div className="resume-privacy">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5 6v5c0 4.5 2.9 8.1 7 10 4.1-1.9 7-5.5 7-10V6l-7-3Z" /><path d="m9 12 2 2 4-5" /></svg>
                  <span>Upload creates a local draft for your review.</span>
                </div>
              </aside>

              <section className="profile-card">
                {pendingFile && (
                  <div className="replace-draft" role="alert">
                    <div>
                      <strong>Replace your unsaved draft?</strong>
                      <p>Parsing {pendingFile.name} will replace the edits currently on screen. Your saved profile and analysis stay unchanged.</p>
                    </div>
                    <div>
                      <button className="button button--ghost" onClick={() => { setPendingFile(null); if (fileInput.current) fileInput.current.value = '' }} type="button">Keep current draft</button>
                      <button className="button button--danger-soft" onClick={() => { const file = pendingFile; setPendingFile(null); parseResume(file) }} type="button">Replace draft</button>
                    </div>
                  </div>
                )}

                {warnings.length > 0 && (
                  <div className="parse-warnings" role="status">
                    <strong>Review notes</strong>
                    <ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
                  </div>
                )}

                {editing ? (
                  <ProfileEditor
                    addSkill={addSkill}
                    busy={uploading || saving}
                    catalog={skillCatalog}
                    onCancel={cancelChanges}
                    onSave={saveProfile}
                    profile={draft}
                    removeSkill={removeSkill}
                    reviewMode={reviewMode}
                    saving={saving}
                    setField={setField}
                    setSkillInput={setSkillInput}
                    setSkillLevel={setSkillLevel}
                    skillError={skillError}
                    skillInput={skillInput}
                  />
                ) : (
                  <ProfileOverview disabled={busy} onEdit={beginEditing} profile={baselineProfile} />
                )}
              </section>
            </section>

            <CareerBoundary><section className="role-finder panel" aria-labelledby="career-recommendations-title">
              <div className="role-finder__heading">
                <div>
                  <p className="section-kicker">Based on your saved profile</p>
                  <h2 id="career-recommendations-title">Recommended Career Roles</h2>
                  <p>
                    ESCO occupations are ranked using profile semantics, essential and optional skills, and saved career interests.
                    Recommendations are local and do not depend on live job availability.
                  </p>
                </div>
                {careerRecommendationData && (
                  <span className="role-finder__count">
                    {careerRecommendationData.considered} roles considered
                  </span>
                )}
              </div>
              <form className="career-role-search" onSubmit={searchCareerRoles}>
                <label>
                  <span>Search occupation titles, alternative titles, or related skills</span>
                  <input
                    disabled={!profileSaved || dirty || !jobPreferencesLoaded}
                    onChange={(event) => setCareerSearchInput(event.target.value)}
                    placeholder="For example, Data Analyst, UX Designer, or Python"
                    type="search"
                    value={careerSearchInput}
                  />
                </label>
                <label>
                  <span>ESCO career category</span>
                  <select
                    disabled={!profileSaved || dirty || !jobPreferencesLoaded || careerRecommendationsLoading}
                    onChange={(event) => setCareerCategory(event.target.value)}
                    value={careerCategory}
                  >
                    <option value="">All categories</option>
                    {(careerRecommendationData?.categories ?? []).map((category) => (
                      <option key={category.code} value={category.code}>
                        {category.name}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="button button--secondary"
                  disabled={!profileSaved || dirty || !jobPreferencesLoaded || careerRecommendationsLoading}
                  type="submit"
                >
                  Search roles
                </button>
                {careerQuery && (
                  <button
                    className="button button--ghost"
                    disabled={careerRecommendationsLoading}
                    onClick={() => { setCareerSearchInput(''); setCareerQuery('') }}
                    type="button"
                  >
                    Clear search
                  </button>
                )}
              </form>
              {!profileSaved && (
                <p className="analysis-lock-message">
                  Save your profile and confirmed skills to get career recommendations.
                </p>
              )}
              {dirty && (
                <p className="analysis-lock-message analysis-lock-message--draft">
                  Save or cancel profile edits to refresh career recommendations.
                </p>
              )}
              {careerRecommendationsError && (
                <div className="live-jobs__error" role="alert">
                  <Alert>{careerRecommendationsError}</Alert>
                  <button
                    className="button button--ghost"
                    disabled={careerRecommendationsLoading}
                    onClick={() => setCareerRefresh((value) => value + 1)}
                    type="button"
                  >
                    Retry recommendations
                  </button>
                </div>
              )}
              {careerRecommendationsLoading && (
                <div className="role-finder__loading">
                  <Spinner label="Matching your profile to ESCO occupations…" />
                </div>
              )}
              {careerRecommendationData?.roles?.length ? (
                <>
                  <p className="role-finder__result-count">
                    Top {careerRecommendationData.roles.length} roles from {careerRecommendationData.source}
                    {careerRecommendationData.release ? ` ${careerRecommendationData.release}` : ''}.
                  </p>
                  <div className="role-match-list">
                    {careerRecommendationData.roles.map((role) => (
                      <article className="role-match-card" key={role.id}>
                        <div className="role-match-card__top">
                          <div>
                            <span className="role-match-card__code">
                              {role.category || role.source}
                            </span>
                            <h3>{role.title}</h3>
                          </div>
                          <div
                            className="role-match-card__score"
                            aria-label={
                              role.compatibility_score === null
                                ? 'Profile compatibility unavailable until profile details are added'
                                : `Estimated profile compatibility ${role.compatibility_score}%`
                            }
                          >
                            <strong>
                              {role.compatibility_score === null
                                ? 'Not assessed'
                                : `${role.compatibility_score}%`}
                            </strong>
                            <span>Estimated compatibility</span>
                          </div>
                        </div>
                        {role.description && (
                          <p className="role-match-card__description">{role.description}</p>
                        )}
                        <p className="role-recommendation__reason">{role.reason}</p>
                        <div className="role-match-card__skills">
                          <div>
                            <strong>Matching skills</strong>
                            {role.matched_skills.length || role.related_skills.length ? (
                              <div className="role-match-chips">
                                {role.matched_skills.map((skill) => (
                                  <span className="role-match-chip role-match-chip--matched" key={`matched-${skill}`}>
                                    {skill}
                                  </span>
                                ))}
                                {role.related_skills.map((skill) => (
                                  <span className="role-match-chip" key={`related-${skill}`}>
                                    {skill} · related
                                  </span>
                                ))}
                              </div>
                            ) : (
                              <p className="role-match-card__muted">No direct skill match; profile context or interests contributed.</p>
                            )}
                          </div>
                          <div>
                            <strong>Missing essential skills</strong>
                            {role.missing_skills.length ? (
                              <div className="role-match-chips">
                                {role.missing_skills.map((skill) => (
                                  <span className="role-match-chip" key={`missing-${skill}`}>{skill}</span>
                                ))}
                              </div>
                            ) : (
                              <p className="role-match-card__muted">No listed core skills are missing from your profile.</p>
                            )}
                          </div>
                          <div className="role-match-card__required">
                            <strong>Essential ESCO skills</strong>
                            <div className="role-match-chips">
                              {role.required_skills.map((skill) => (
                                <span className="role-match-chip" key={`required-${skill}`}>{skill}</span>
                              ))}
                            </div>
                          </div>
                          {role.optional_skills.length > 0 && (
                            <div className="role-match-card__required">
                              <strong>Optional ESCO skills</strong>
                              <div className="role-match-chips">
                                {role.optional_skills.map((skill) => (
                                  <span className="role-match-chip" key={`optional-${skill}`}>{skill}</span>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                        <button
                          className="button button--secondary role-match-card__action"
                          disabled={jobLoading || jobPreferencesDirty}
                          onClick={() => viewAvailableJobs(role)}
                          type="button"
                        >
                          View Available Jobs
                        </button>
                      </article>
                    ))}
                  </div>
                </>
              ) : careerRecommendationData ? (
                <div className="role-finder__empty">
                  <strong>No career roles could be matched yet.</strong>
                  <span>{careerRecommendationData.message || 'Add confirmed skills, education, or experience to your saved profile.'}</span>
                </div>
              ) : null}
            </section>

            </CareerBoundary>
            <CareerBoundary message="Live jobs could not be displayed. Your profile and dashboard are still available.">
            <section className="live-jobs panel" aria-labelledby="live-jobs-title" ref={jobSectionRef}>
              <div className="live-jobs__heading">
                <div>
                  <p className="section-kicker">Explore Job Opportunities</p>
                  <h2 id="live-jobs-title">Find matching jobs</h2>
                  <p>Current public listings from Himalayas and configured company career boards are ranked against your saved skills, preferred roles, experience, and qualifications.</p>
                </div>
                <span className="live-jobs__market">Live public listings</span>
              </div>

              {!profileSaved && (
                <p className="analysis-lock-message">Save your profile and skills before searching for personalized jobs.</p>
              )}
              {dirty && (
                <p className="analysis-lock-message analysis-lock-message--draft">
                  Save or cancel your resume/profile edits to refresh job recommendations.
                </p>
              )}

              <form className="live-jobs__preferences" onSubmit={saveJobPreferences}>
                <div className="live-jobs__form-heading">
                  <div>
                    <h3>Your job preferences</h3>
                    <p>These saved preferences are applied to each live search.</p>
                  </div>
                  {jobPreferencesDirty && <span className="live-jobs__unsaved">Unsaved preferences</span>}
                </div>
                <div className="live-jobs__preference-grid">
                  <label className="live-jobs__roles">
                    <span>Preferred job roles <small>(comma separated, up to 6)</small></span>
                    <input
                      disabled={!profileSaved || dirty || !jobPreferencesLoaded || jobPreferencesSaving}
                      onChange={(event) => {
                        setPreferredRolesInput(event.target.value)
                        setJobPreferencesError('')
                      }}
                      placeholder="e.g. Data Analyst, Software Engineer"
                      type="text"
                      value={preferredRolesInput}
                    />
                  </label>
                  <label>
                    <span>Preferred location</span>
                    <input
                      disabled={!profileSaved || dirty || !jobPreferencesLoaded || jobPreferencesSaving}
                      onChange={(event) => setJobPreferencesDraft((value) => ({ ...value, location: event.target.value }))}
                      placeholder="India, Bengaluru, Pune…"
                      type="text"
                      value={jobPreferencesDraft.location}
                    />
                  </label>
                  <label>
                    <span>Experience level</span>
                    <select
                      disabled={!profileSaved || dirty || !jobPreferencesLoaded || jobPreferencesSaving}
                      onChange={(event) => setJobPreferencesDraft((value) => ({ ...value, experience_level: event.target.value }))}
                      value={jobPreferencesDraft.experience_level}
                    >
                      <option value="any">Any experience</option>
                      <option value="internship">Internship</option>
                      <option value="entry">Entry level / fresher</option>
                      <option value="mid">Mid level</option>
                      <option value="senior">Senior</option>
                    </select>
                  </label>
                  <label>
                    <span>Employment type</span>
                    <select
                      disabled={!profileSaved || dirty || !jobPreferencesLoaded || jobPreferencesSaving}
                      onChange={(event) => setJobPreferencesDraft((value) => ({ ...value, employment_type: event.target.value }))}
                      value={jobPreferencesDraft.employment_type}
                    >
                      <option value="any">Any type</option>
                      <option value="full_time">Full-time</option>
                      <option value="part_time">Part-time</option>
                      <option value="contract">Contract</option>
                      <option value="internship">Internship</option>
                    </select>
                  </label>
                  <label>
                    <span>Career category</span>
                    <select
                      disabled={!profileSaved || dirty || !jobPreferencesLoaded || jobPreferencesSaving}
                      onChange={(event) => setJobPreferencesDraft((value) => ({ ...value, career_category: event.target.value }))}
                      value={jobPreferencesDraft.career_category}
                    >
                      <option value="">All categories</option>
                      <option value="it-jobs">IT &amp; technology</option>
                      <option value="accounting-finance-jobs">Accounting &amp; finance</option>
                      <option value="engineering-jobs">Engineering</option>
                      <option value="sales-jobs">Sales</option>
                      <option value="customer-services-jobs">Customer service</option>
                      <option value="hr-jobs">Human resources</option>
                      <option value="healthcare-nursing-jobs">Healthcare</option>
                      <option value="teaching-jobs">Education</option>
                      <option value="creative-design-jobs">Creative &amp; design</option>
                      <option value="admin-jobs">Administration</option>
                      <option value="other-general-jobs">Other</option>
                    </select>
                  </label>
                  <label>
                    <span>Work mode</span>
                    <select
                      disabled={!profileSaved || dirty || !jobPreferencesLoaded || jobPreferencesSaving}
                      onChange={(event) => setJobPreferencesDraft((value) => ({ ...value, work_mode: event.target.value }))}
                      value={jobPreferencesDraft.work_mode}
                    >
                      <option value="any">Any work mode</option>
                      <option value="remote">Remote</option>
                      <option value="hybrid">Hybrid</option>
                      <option value="on_site">On-site</option>
                    </select>
                  </label>
                  <label>
                    <span>Expected minimum salary (INR, optional)</span>
                    <input
                      disabled={!profileSaved || dirty || !jobPreferencesLoaded || jobPreferencesSaving}
                      max="1000000000"
                      min="0"
                      onChange={(event) => setJobPreferencesDraft((value) => ({
                        ...value,
                        minimum_salary: event.target.value === '' ? null : Number(event.target.value),
                      }))}
                      placeholder="No minimum"
                      step="1"
                      type="number"
                      value={jobPreferencesDraft.minimum_salary ?? ''}
                    />
                  </label>
                </div>
                {jobPreferencesError && <Alert>{jobPreferencesError}</Alert>}
                <button
                  className="button button--secondary"
                  disabled={!profileSaved || dirty || !jobPreferencesLoaded || jobPreferencesSaving}
                  type="submit"
                >
                  {jobPreferencesSaving ? <Spinner label="Saving preferences…" /> : 'Save preferences'}
                </button>
              </form>

              <form className="live-jobs__search" onSubmit={searchLiveJobs}>
                <label>
                  <span>Job title or keywords</span>
                  <input
                    disabled={!profileSaved || dirty || jobPreferencesDirty || !jobPreferencesLoaded}
                    onChange={(event) => setJobSearchInput(event.target.value)}
                    placeholder="Optional title or keyword"
                    type="search"
                    value={jobSearchInput}
                  />
                </label>
                <label>
                  <span>Sort results</span>
                  <select
                    disabled={!profileSaved || dirty || jobPreferencesDirty || !jobPreferencesLoaded}
                    onChange={(event) => {
                      setJobSort(event.target.value)
                      setJobPage(1)
                    }}
                    value={jobSort}
                  >
                    <option value="relevance">Best profile match</option>
                    <option value="date">Most recent</option>
                    <option value="salary_desc">Highest salary</option>
                    <option value="salary_asc">Lowest salary</option>
                  </select>
                </label>
                <button
                  className="button button--analyze"
                  disabled={!profileSaved || dirty || jobPreferencesDirty || !jobPreferencesLoaded || jobLoading}
                  type="submit"
                >
                  {jobLoading ? <Spinner label="Searching…" /> : 'Find matching jobs'}
                </button>
              </form>
              <p className="live-jobs__privacy">
                Search keywords go to public job providers and configured company boards are filtered locally.
                Resume documents, contact details, education, and experience text stay local. Other filters use available listing details.
              </p>

              {jobError && (
                <div className="live-jobs__error">
                  <Alert>{jobError}</Alert>
                  <button
                    className="button button--ghost"
                    disabled={jobLoading}
                    onClick={() => setJobRefresh((value) => value + 1)}
                    type="button"
                  >
                    Retry search
                  </button>
                </div>
              )}
              {jobSearchData?.provider_warnings?.length > 0 && (
                <Alert>
                  Some providers were unavailable: {[...new Set(
                    jobSearchData.provider_warnings.map((warning) =>
                      `${warning.provider ? `${warning.provider}: ` : ''}${warning.error}`),
                  )].join(' ')}
                </Alert>
              )}
              {jobPreferencesDirty && jobSearchData && (
                <p className="analysis-lock-message analysis-lock-message--draft">
                  Results still use your last saved preferences. Save the changes to refresh them.
                </p>
              )}
              {jobLoading && <div className="role-finder__loading"><Spinner label="Fetching and ranking real job listings…" /></div>}
              {jobSearchData?.jobs?.length ? (
                <>
                  <p className="live-jobs__result-count">
                    Showing {jobSearchData.jobs.length} of {Number(jobSearchData.total).toLocaleString()} matching listings
                    {jobSearchData.location ? ` for ${jobSearchData.location}` : ''}.
                  </p>
                  <div className="live-job-list">
                    {jobSearchData.jobs.map((job) => (
                      <article className="live-job-card" key={job.id}>
                        <div className="live-job-card__heading">
                          <div>
                            <div className="live-job-card__badges">
                              <span>{job.source || job.category || 'Job listing'}</span>
                              {job.category && job.category !== job.source && <span>{job.category}</span>}
                              {job.is_alternative && <span className="live-job-card__alternative">Related role</span>}
                            </div>
                            <h3>{job.title}</h3>
                            <p>{job.company || 'Company not listed'}{` · ${job.location || 'Location not listed'}`}</p>
                          </div>
                          <div
                            className="live-job-card__score"
                            aria-label={job.skill_match_percentage == null
                              ? 'Skills match unavailable'
                              : `Skills match ${job.skill_match_percentage}%`}
                          >
                            <strong>{job.skill_match_percentage == null
                              ? '—'
                              : `${job.skill_match_percentage}%`}
                            </strong>
                            <span>Skills match</span>
                            <small>
                              {job.compatibility_score == null
                                ? 'Profile fit unavailable'
                                : `${job.compatibility_score}% profile fit`}
                            </small>
                          </div>
                        </div>
                        <div className="live-job-card__facts">
                          <span><strong>Employment</strong>{formatJobType(job) || 'Not listed'}</span>
                          <span><strong>Work mode</strong>{formatWorkMode(job.work_mode)}</span>
                          {(
                            <span>
                              <strong>Salary</strong>
                              {formatJobSalary(job) || 'Not listed'}
                              {job.salary_is_predicted ? ' · provider estimate' : ''}
                            </span>
                          )}
                          {(
                            <span><strong>Posted / updated</strong>{formatJobDate(job.created) || 'Not listed'}</span>
                          )}
                        </div>
                        <div className="live-job-card__skills">
                          <div>
                            <strong>Profile skills found in listing</strong>
                            {job.matched_skills.length
                              ? <p>{job.matched_skills.join(', ')}</p>
                              : <p className="live-job-card__muted">No saved skills were explicitly mentioned.</p>}
                          </div>
                          <div>
                            <strong>Other skills mentioned <small>(not verified requirements)</small></strong>
                            {job.missing_skills.length
                              ? <p>{job.missing_skills.slice(0, 8).join(', ')}</p>
                              : <p className="live-job-card__muted">No additional catalog skills detected.</p>}
                          </div>
                        </div>
                        <details className="live-job-card__details">
                          <summary>View Details</summary>
                          <p className="live-job-card__description">
                            {job.description || 'The provider did not include a description.'}
                          </p>
                          {job.unknown_match_data?.length > 0 && (
                            <p className="live-job-card__muted">
                              Not enough listing information to compare: {job.unknown_match_data.join(', ')}.
                            </p>
                          )}
                          {job.reasons.length > 0 && (
                            <ul className="live-job-card__reasons">
                              {job.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                            </ul>
                          )}
                        </details>
                        <footer className="live-job-card__footer">
                          <a
                            className="button button--secondary"
                            href={job.application_url}
                            rel="noopener noreferrer"
                            target="_blank"
                          >
                            Apply Now
                          </a>
                          <a
                            aria-label={`Job listing provided by ${job.source || 'a public provider'}`}
                            className="job-provider-attribution"
                            href={job.source_url}
                            rel="noopener noreferrer"
                            target="_blank"
                          >
                            {job.source || 'Public provider'}
                          </a>
                        </footer>
                      </article>
                    ))}
                  </div>
                  {!jobSearchData.has_direct_role_match && jobSearchData.alternative_role_suggestions?.length > 0 && (
                    <div className="live-jobs__alternatives">
                      <strong>No direct title match on this page. Try a related role:</strong>
                      <p>These are search suggestions, not vacancies. Your saved location, salary, and employment filters remain unchanged.</p>
                      <div>
                        {jobSearchData.alternative_role_suggestions.map((role) => (
                          <button
                            className="live-job-card__alternative-button"
                            disabled={jobLoading || jobPreferencesDirty}
                            key={role}
                            onClick={() => searchAlternativeRole(role)}
                            type="button"
                          >
                            Search {role}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : jobSearchData ? (
                <div className="role-finder__empty">
                  <strong>No live listings were found for these preferences.</strong>
                  <span>Try a related role suggestion or adjust your search filters. No sample or generated vacancies are shown.</span>
                  {jobSearchData.alternative_role_suggestions?.length > 0 && (
                    <div className="live-jobs__alternatives">
                      <strong>Related role searches</strong>
                      <div>
                        {jobSearchData.alternative_role_suggestions.map((role) => (
                          <button
                            className="live-job-card__alternative-button"
                            disabled={jobLoading || jobPreferencesDirty}
                            key={role}
                            onClick={() => searchAlternativeRole(role)}
                            type="button"
                          >
                            Search {role}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : null}
                  {Number(jobSearchData?.page_count) > 1 && (
                    <nav className="live-jobs__pagination" aria-label="Job results pages">
                      <button
                        className="button button--ghost"
                        disabled={jobPage <= 1 || jobLoading}
                        onClick={() => setJobPage((page) => Math.max(1, page - 1))}
                        type="button"
                      >
                        Previous
                      </button>
                      <span>Page {jobPage} of {jobSearchData.page_count}</span>
                      <button
                        className="button button--ghost"
                        disabled={jobPage >= jobSearchData.page_count || jobLoading}
                        onClick={() => setJobPage((page) => page + 1)}
                        type="button"
                      >
                        Next
                      </button>
                    </nav>
                  )}
            </section>

            </CareerBoundary>
            <section className="employee-analysis-controls" aria-labelledby="employee-analysis-title">
              <div className="employee-analysis-controls__copy">
                <span className="analysis-controls__step" aria-hidden="true">
                  <svg viewBox="0 0 24 24"><path d="M4 19V9M10 19V5M16 19v-7M22 19V3" /></svg>
                </span>
                <div>
                  <p className="section-kicker">Saved profile analysis</p>
                  <h2 id="employee-analysis-title">Compare yourself with a target role</h2>
                  <p>Only saved profile information is used, so draft edits can never change a result by accident.</p>
                </div>
              </div>
              <div className="employee-role-control">
                <SelectField
                  disabled={analyzing}
                  id="employee-target-role"
                  label="Target job role"
                  onChange={updateRole}
                  optionLabel={(role) => role.name}
                  options={roles}
                  placeholder="Select a target role"
                  value={roleId}
                />
                <button
                  className="button button--analyze"
                  disabled={!profileSaved || dirty || !roleId || analyzing}
                  onClick={runAnalysis}
                  type="button"
                >
                  {analyzing ? <Spinner label="Analyzing…" /> : 'Analyze my skills'}
                </button>
              </div>
              {!profileSaved && <p className="analysis-lock-message">Save your profile before running an analysis.</p>}
              {dirty && <p className="analysis-lock-message analysis-lock-message--draft">Save or cancel your draft to analyze the saved profile.</p>}
            </section>

            {dirty && analysis && (
              <Alert variant="info">This result uses your last saved profile. Save your draft and run the analysis again to update it.</Alert>
            )}
            {analyzing && !analysis ? <AnalysisLoading /> : analysis ? <Results analysis={analysis} /> : (
              <section className="employee-analysis-empty">
                <span aria-hidden="true">
                  <svg viewBox="0 0 48 48"><path d="M8 38h32M12 34V22M22 34V12M32 34V18M38 34V8" /></svg>
                </span>
                <div>
                  <p className="section-kicker">No analysis yet</p>
                  <h2>See where you already shine</h2>
                  <p>Save your profile, choose a target role, and compare current ratings with its requirements.</p>
                </div>
              </section>
            )}
          </>
        )}
      </main>
      <footer className="app-footer">
        <span>SkillBridge</span>
        <span>Your profile stays in this local demo workspace</span>
      </footer>
    </div>
  )
}
