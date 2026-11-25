import { Component } from 'react'

const label = value => typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
const list = value => Array.isArray(value) ? value : []
const skillLabels = value => list(value).map(item => label(item) || label(item?.name) || label(item?.preferred_label)).filter(Boolean)

export function normalizeCareers(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.roles)) {
    throw new Error('The career service returned an invalid response. Please retry.')
  }
  return {
    ...value, source: label(value.source), release: label(value.release),
    message: label(value.message), considered: Number(value.considered) || 0,
    categories: list(value.categories).map(item => ({
      code: label(item?.code) || label(item), name: label(item?.name) || label(item),
    })).filter(item => item.code),
    roles: value.roles.filter(item => item && typeof item === 'object').map((item, index) => ({
      ...item, id: label(item.id) || String(index), title: label(item.title) || 'Untitled ESCO occupation',
      category: label(item.category) || label(item.category?.name), source: label(item.source),
      reason: label(item.reason), description: label(item.description),
      compatibility_score: typeof item.compatibility_score === 'number' && Number.isFinite(item.compatibility_score) ? item.compatibility_score : null,
      ...Object.fromEntries(['matched_skills','related_skills','missing_skills','required_skills','optional_skills'].map(key => [key, skillLabels(item[key])])),
    })),
  }
}

export class CareerBoundary extends Component {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error, info) { console.error('Career recommendation rendering failed', error, info.componentStack) }
  render() {
    return this.state.failed ? <section className="role-finder panel" role="alert">
      <p>{this.props.message || 'Career recommendations could not be displayed. Your profile and dashboard are still available.'}</p>
      <button type="button" className="button button--secondary" onClick={() => this.setState({ failed: false })}>Retry display</button>
    </section> : this.props.children
  }
}

// Public vacancy responses still require validation before entering React state.
export function normalizeJobs(value) {
  if (!value || !Array.isArray(value.jobs)) throw new Error('Invalid job-search response. Please retry.')
  const textKeys = ['id','title','company','location','description','salary_text','created',
    'employment_type','employment_type_label','contract_time','category','work_mode','source','source_url']
  const numeric = value => typeof value === 'number' && Number.isFinite(value) ? value : null
  const safeLink = value => {
    try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password &&
      Boolean(url.hostname) ? url.href : '' } catch { return '' }
  }
  return { ...value, location: label(value.location),
    providers: skillLabels(value.providers),
    provider_warnings: list(value.provider_warnings).map(item => ({
      provider: label(item?.provider), error: label(item?.error),
    })).filter(item => item.error),
    total: Number(value.total) || 0, page_count: Number(value.page_count) || 0,
    alternative_role_suggestions: skillLabels(value.alternative_role_suggestions),
    search_queries: skillLabels(value.search_queries), search_roles: skillLabels(value.search_roles),
    jobs: value.jobs.filter(item => item && typeof item === 'object').map(item => ({
      ...item, ...Object.fromEntries(textKeys.map(key => [key, label(item[key])])),
      ...Object.fromEntries(['matched_skills','missing_skills','reasons','unknown_match_data'].map(key => [key, skillLabels(item[key])])),
      ...Object.fromEntries(['salary_min','salary_max','compatibility_score','skill_match_percentage'].map(key => [key, numeric(item[key])])),
      application_url: safeLink(item.application_url), source_url: safeLink(item.source_url),
    })).filter(item => item.id && item.title && item.application_url),
  }
}
