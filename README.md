# Employee Skill Gap Analysis & Training Recommendation System

A college mini project with two workflows: HR administrators compare fictional employee ratings with target roles, and employees register, review a locally parsed resume, confirm their own skill ratings, and receive a training plan. The employee dashboard can also retrieve genuine live vacancies from public job feeds and configured company career boards; resume parsing remains local.

## What the project includes

- HR admin and employee login with Argon2 password hashing and backend-enforced permissions
- Employee registration with unique usernames; administrator creation remains a local setup command
- PDF/DOCX resume upload, editable extracted details, and explicit review before saving
- Private SQLite employee profiles, manually editable skills, and self-reported ratings
- Expiring, server-side sessions stored in SQLite
- Five fictional employees and three target roles
- Automatic profile-based ESCO career recommendations using local semantic embeddings, independent of live job availability
- O*NET occupation and skill data retained for the existing skill-gap analysis
- Personalized live vacancies from Himalayas, public Greenhouse boards, and configured Lever boards without API credentials
- Job-title and keyword search, career category, location, experience, employment type, work mode, salary, sorting, and pagination
- Skill-gap calculation on a 0–5 proficiency scale
- Clear handling of unassessed skills versus recorded level `0`
- Course recommendations prioritized by the employee's current level
- Responsive React dashboard with summary cards, chart, table, and course groups
- Demo cases for matched training, an unassessed skill, no catalog match, and a fully qualified employee

## Career search stability and local startup

Run both services from this checkout. A frontend from another copy can have an
incompatible response contract: ESCO categories are `{code, name}` objects, not
React text children. Check port owners with `lsof -iTCP:5173 -sTCP:LISTEN` and
`lsof -iTCP:8000 -sTCP:LISTEN`, then inspect their directories with
`lsof -a -p <PID> -d cwd`. Stop the unwanted process before starting:

```sh
cd "/Users/mohit/vscode/mini project copy/backend"
.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```sh
cd "/Users/mohit/vscode/mini project copy/frontend"
npm run dev -- --host localhost
```

Open `http://localhost:5173`. Vite uses a strict port so another server cannot
silently force this checkout onto a different URL. The frontend uses the Vite
API proxy. If npm is unavailable, install Node.js and reopen your terminal.

Career responses are validated before rendering; the recommendation section has
its own error boundary. Searches retain previous results, disable duplicate
submissions, cancel obsolete requests, and time out after 45 seconds. Failures
show an inline retry action. An obsolete response cannot overwrite a newer one.

The backend caches the ESCO catalog, relationships, and stored vectors once per
process, reuses its Sentence Transformer, and keeps a bounded cache of small
embedding batches. Restart the backend after importing or rebuilding ESCO data.
The synchronous recommendation endpoint runs in FastAPI's worker thread pool.
Cold model startup can still be slower than subsequent searches. Results remain
limited to 10; the existing live vacancy pagination is unchanged.

The common search names `Full Stack Developer` and `UI/UX Designer` map to actual
ESCO `web developer` and `user interface designer` labels. They do not create
occupations or vacancies. Recommendations use confirmed saved resume details;
unsaved edits must be saved first. Related skill matches are estimates, and ESCO
does not provide comparable 0–5 employee proficiency ratings.

Regression checks: `cd backend && .venv/bin/python -m unittest discover` and
`cd frontend && npm run build`. Browser checks performed with real local ESCO
responses include the six requested searches, automatic and empty queries,
repeated searches, and rapid submissions. Delayed, empty, malformed, failed, and
timed-out career responses were separately injected in the browser to verify
that the dashboard remains visible; these were fault tests, not job listings.

## Technology

- Frontend: React, Vite, JavaScript, plain CSS
- Backend: Python, FastAPI, Uvicorn, Pydantic 2 request validation, and `httpx` asynchronous public-provider requests
- Accounts, sessions, and confirmed employee profiles: SQLite
- HR demo ratings, role requirements, and course catalog: local CSV files
- ESCO occupations, skills, essential/optional links, and semantic vectors: locally imported into SQLite
- O*NET occupation and skill data: separately imported for the existing skill-gap workflow
- Sentence Transformers: local `all-MiniLM-L6-v2` embeddings
- Local resume parsing: `pypdf`, `python-docx`, and `python-multipart`

Recommended versions: Python 3.9 or newer and Node.js `20.19+` or `22.12+`.

## Project structure

```text
.
├── .env.example
├── backend/
│   ├── data/
│   │   ├── courses.csv
│   │   ├── employees.csv
│   │   ├── role_requirements.csv
│   │   └── skill_ratings.csv
│   ├── main.py
│   ├── download_esco_api.py
│   ├── import_esco.py
│   ├── prepare_esco_embeddings.py
│   ├── career_recommendations.py
│   ├── import_onet.py
│   ├── job_search.py
│   ├── public_job_providers.py
│   ├── onet_matching.py
│   ├── resume_parser.py
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── setup_admin.py
│   ├── test_app.py
│   ├── test_public_jobs.py
│   └── sample_resumes/
│       ├── generate_samples.py
│       ├── maya_shah.pdf
│       └── maya_shah.docx
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   └── styles.css
│   ├── index.html
│   ├── package-lock.json
│   ├── package.json
│   └── vite.config.js
├── .gitignore
└── README.md
```

`backend/main.py` provides the authenticated API, SQLite sessions and employee profiles, and both the HR and employee analysis routes. `backend/public_job_providers.py` fetches and normalizes keyless job feeds; `backend/job_search.py` applies local profile filtering, ranking, and pagination. `backend/import_esco.py` imports the local career catalog; `backend/prepare_esco_embeddings.py` prepares its model vectors. O*NET remains available through `backend/import_onet.py` for the existing skill-gap workflow. `backend/resume_parser.py` extracts PDF/DOCX content locally without retaining the uploaded file.

No default credentials are shipped, and the local SQLite database is ignored by Git. Create the HR administrator locally during first setup. Employees create their accounts using the **Employee registration** tab on the sign-in screen.

## Updating an existing installation

Stop the backend, activate its existing virtual environment, and run:

```bash
cd backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

The backend upgrades the old admin-only SQLite schema automatically and keeps the existing administrator password and active sessions. Do not delete `skill_gap.db` or rerun admin setup for an existing account. The frontend still runs with `npm run dev` from `frontend/`; it does not need any new runtime packages.

### Import the ESCO career catalog

ESCO career recommendations use the official English classification, not the live jobs provider or O*NET. The direct CSV download on the ESCO page requires accepting the notice and providing an email address. To initialize without that email step, the project can export the same selected release from the official ESCO API into the CSV format expected by the importer:

```bash
cd backend
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python download_esco_api.py --version 1.2.1 --destination data/esco-1.2.1 --occupation-page-size 100 --skill-page-size 500
./.venv/bin/python import_esco.py --source-dir data/esco-1.2.1 --version 1.2.1 --device cpu
```

The API exporter retrieves English ESCO v1.2.1 occupations, skills, essential/optional links, and writes `occupations_en.csv`, `skills_en.csv`, and `occupationSkillRelations.csv`. It uses the [official ESCO REST API](https://ec.europa.eu/esco/api/doc/esco_api_doc.html); occupation pages default to 100 and skill pages to 500 records to balance request reliability and runtime. It may take several minutes, retries transient errors, and writes the completed files only after all pages validate. ESCO notes that its current API version is provided for reference and may be replaced, so if API export fails, use the direct package flow: download the **classification** package for **ESCO v1.2.1**, language **English (`en`)**, and file type **CSV** from the [official ESCO download page](https://esco.ec.europa.eu/en/use-esco/download), accept its privacy notice, provide an email address for the download link, and extract the package under `backend/data/esco-1.2.1/`. The importer searches nested directories and accepts both `occupationSkillRelations.csv` and `occupationSkillRelations_en.csv`.

The validated v1.2.1 English import contains **3,039 occupations, 14,579 skills, and 126,051 unique essential/optional occupation-skill relations**, with 17,618 cached vectors (one per occupation and skill). The importer replaces only ESCO catalog tables, preserves users, confirmed profiles, HR sample records, and O*NET skill-gap tables, and reports imported row counts. It also computes and caches normalized occupation and skill vectors with `sentence-transformers/all-MiniLM-L6-v2` in SQLite. Offline embedding preparation defaults to CPU to avoid exhausting shared GPU/MPS memory; `--device mps` or `--device cuda` can be selected on machines with available accelerator memory. The first run downloads that public model from Hugging Face; following recommendation requests are local and do not call ESCO or a job-vacancy API. Keep the downloaded dataset in the ignored local data directory; it is not committed.

The project has no Dockerfile or Compose file. In a container deployment, place or mount the three CSV files beneath `data/esco-1.2.1/` next to `import_esco.py`, then run the same import command inside the backend container. Ensure the container can read the dataset and uses the same persistent `DATABASE_PATH` as the running API; importing into a different SQLite file will not initialize the API's catalog.

```bash
./.venv/bin/python import_esco.py --source-dir /path/to/extracted/english-csv --version 1.2.1
# To import without computing embeddings, then compute them later:
./.venv/bin/python import_esco.py --source-dir data/esco-1.2.1 --skip-embeddings
./.venv/bin/python prepare_esco_embeddings.py --device cpu
```

Career recommendations return the top 10 local occupation profiles automatically from the signed-in employee's saved skills, education, work experience, and career interests. The current dashboard uses the authenticated `GET /api/career-recommendations` route. `GET /api/matches` remains as a compatibility route for older dashboard builds and accepts `q`, `category`, `page`, and `page_size`; it uses the same ESCO recommender and returns both `roles` and `matches`. Manual search accepts occupation titles, alternative titles, and linked skills. Missing catalog data and missing model vectors are reported with setup instructions; no substitute role or vacancy data is fabricated.

**Attribution:** ESCO, the European Commission's European Skills, Competences, Qualifications and Occupations classification, release v1.2.1. Sources: [official ESCO dataset download](https://esco.ec.europa.eu/en/use-esco/download), [official ESCO REST API](https://ec.europa.eu/esco/api/doc/esco_api_doc.html), and [dataset structure documentation](https://esco.ec.europa.eu/en/structure-esco-downloadable-datasets). Occupation and skill URIs are preserved as stable concept identifiers; ESCO's relation type distinguishes essential from optional skills.

### Import the O*NET skill-gap catalog (legacy workflow)

O*NET remains available for the separate target-role analysis and comparable proficiency-level gaps. It is not used by career recommendations. From `backend/`, with the virtual environment active, run:

```bash
python import_onet.py
```

The importer downloads official O*NET CSV files over HTTPS and replaces only O*NET catalog tables. To import files downloaded yourself:

```bash
python import_onet.py --source-dir /path/to/onet-31-csv --database skill_gap.db
```

#### Dataset, attribution, and rating scales

This project uses the **O*NET 31.0 Database**, published by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA), from the [official O*NET database download page](https://www.onetcenter.org/database.html). Occupation descriptions and skill records are imported from the release's `occupation_data.csv`, `essential_skills.csv`, `transferable_skills.csv`, and `software_skills.csv`.

The database includes 1,016 occupation titles. This import includes **923 occupations with usable skill records and 60,022 deduplicated occupation-skill records** (910 occupations have Essential/Transferable ratings; software-skill examples cover 923). The O*NET occupation code is the stable role ID. The category filter is derived from the first two digits of the code, which identify a 2018 SOC major group.

O*NET's **Importance (IM)** rating (1–5) measures how important a rated skill is to the occupation. It is stored and used only as a score weight, never as a proficiency requirement. The separate **Level (LV)** rating (0–7) estimates the level required for an occupation. For the existing employee 0–5 self-rating UI, LV is linearly rescaled to 0–5 (`LV × 5 ÷ 7`, rounded to one decimal). A numeric gap is shown only when the employee has a self-reported rating and the occupation has an LV rating for that same mapped skill. A resume mention alone remains **Unassessed** and does not create a numeric gap. O*NET software-skill workplace examples do not have IM/LV ratings; they contribute to overlap with equal weight and do not imply proficiency. “Skill match” is an estimate based on confirmed profile skill overlap, not a qualification, hiring, or career-success prediction.

Aliases such as JS/JavaScript, SQL/PostgreSQL/MySQL, Excel/Microsoft Excel, and Tableau/Power BI are normalized for matching. Skills not in the local course catalog can still affect a match or be listed as unconfirmed; they show “No matching course in the catalog” when a training recommendation is available but there is no corresponding course.

**Attribution:** This product includes information from the O*NET 31.0 Database by USDOL/ETA, used under the [Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/). This project normalizes skill aliases, derives broad occupation categories, and applies the matching and level-rescaling rules described above. USDOL/ETA has not approved, endorsed, or tested these modifications. O*NET® is a trademark of USDOL/ETA.

## Setup on macOS or Linux

From the project root, prepare the backend:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python setup_admin.py
```

The setup command asks for a username and uses hidden password prompts. The password must contain at least eight characters.

Prepare the frontend in another terminal:

```bash
cd frontend
npm install
```

## Setup on Windows PowerShell

From the project root, prepare the backend:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python setup_admin.py
```

If PowerShell blocks virtual-environment activation, run this once in that terminal and then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Prepare the frontend in another terminal:

```powershell
cd frontend
npm install
```

## Run the project

Keep both terminals open.

Terminal 1 — start the API from `backend/` with the virtual environment active:

```bash
python -m uvicorn main:app --reload
```

Terminal 2 — start the React app from `frontend/`:

```bash
npm run dev
```

Open **http://localhost:5173**. Sign in with your existing HR admin account or register an employee account. The server assigns the employee role during registration; there is no public admin registration. Use `localhost`, not `127.0.0.1`, unless you also change `FRONTEND_ORIGIN` as described below.

To check the frontend production build:

```bash
cd frontend
npm run build
```

## Local configuration

The defaults work for the commands above. Set environment variables before launching Uvicorn only when you need to change them.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | Exact browser origin allowed for state-changing requests and CORS |
| `SESSION_COOKIE_SECURE` | `false` | Keep `false` for local HTTP; use `true` behind HTTPS |
| `SESSION_TTL_HOURS` | `8` | Login-session lifetime |
| `DATABASE_PATH` | `backend/skill_gap.db` | Optional alternate SQLite path |
| `MAX_FAILED_LOGINS` | `5` | Failed attempts allowed in the rate-limit window |
| `LOGIN_WINDOW_SECONDS` | `900` | Failed-login rate-limit window |
| `GREENHOUSE_BOARD_TOKENS` | `stripe,figma,gitlab,cloudflare,databricks` | Optional comma-separated public Greenhouse board slugs |
| `LEVER_SITE_NAMES` | `mindtickle,meesho,cred` | Optional comma-separated public Lever board slugs |
| `APP_ENV` | unset | Set to `development` to log privacy-safe job-search diagnostics |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | FastAPI URL used by Vite's `/api` proxy |
| `VITE_DEV_HOST` | `127.0.0.1` | Set to `0.0.0.0` to expose Vite from a container |

### Keyless public job search

The backend fetches real postings from three public sources without API keys or
registration:

- [Himalayas job API](https://himalayas.app/jobs/api/search): GET search requests
  use `q` and `page`; the backend requests separate role queries and up to two
  pages per query. India eligibility is taken from provider location/time-zone
  restrictions and is not assumed when the provider omits that information.
- [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html):
  public GET requests use
  `https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true`.
  The configured slugs are public company-board identifiers, not credentials.
- [Lever Postings API](https://github.com/lever/postings-api): public GET
  requests use `https://api.lever.co/v0/postings/{site}?mode=json&skip=...&limit=...`.
  Lever does not provide full-text cross-company search; the selected boards are
  downloaded and filtered/ranked locally.

The default Greenhouse boards (`stripe`, `figma`, `gitlab`, `cloudflare`, and
`databricks`) and Lever sites (`mindtickle`, `meesho`, and `cred`) are a small, replaceable sample, not
comprehensive India-wide coverage. Set `GREENHOUSE_BOARD_TOKENS` and
`LEVER_SITE_NAMES` in the backend environment to choose other publicly available
boards. An empty value disables that board provider. Provider availability,
rate limits, and job coverage are controlled by each provider; failed providers
are reported while successful ones continue to supply results. No job-site
scraping, generated vacancy, or paid API is used.

The authenticated employee's saved preferred roles are used first. If none are
saved, automatic ESCO role recommendations are used, with a skill-based fallback
when the catalog is unavailable. A manual title/keyword search remains available.
Only search terms are sent to the Himalayas search endpoint; resume files,
contact details, education, work history, and individual ratings are not sent.
Greenhouse and Lever board results are downloaded by the backend and filtered
locally.

Job-board responses are cached for 15 minutes in
`public_job_cache.sqlite3` beside the configured SQLite database; duplicate
in-flight requests are coalesced. The cache contains only public provider
responses, not employee profiles or personalized scores. Results are deduplicated
and re-ranked locally for the signed-in employee using role relevance (35%),
technical skills (35%), experience (15%), location (10%), and other available
preferences (5%). The skill-match estimate is the overlap between saved profile
skills and detected listing skills, relative to the combined known skill sets;
it remains unknown when listings contain no detectable skills. Explicitly
conflicting location, experience, employment, work mode, or salary preferences
are filtered when listing data is available; missing
listing fields remain unknown rather than being fabricated. Salary comparisons
are only calculated when currency and pay period are known. Skills found in job
text are mentions, not verified requirements.

Set `APP_ENV=development` to log generated role queries, preference fields,
provider status/counts, filtered count, and final recommendation count. The
backend does not log employee names, contact details, resume text, or provider
credentials. See the linked provider documentation and respect their published
terms and rate limits; response caching and bounded concurrent requests avoid
excessive calls.

#### Docker and API proxy

This workspace has no Dockerfile or Compose configuration. The Vite development proxy now reads `VITE_API_PROXY_TARGET` from the root `.env` (default `http://127.0.0.1:8000`). In a Docker network, set it to the backend service name, for example `http://backend:8000`, set `VITE_DEV_HOST=0.0.0.0`, and bind Uvicorn to `0.0.0.0` so both servers are reachable from their peers/host. No provider credentials are needed; optional Greenhouse/Lever board lists may be set in the backend container environment. If the frontend runs directly on the host while the backend runs in Docker, use the backend's host-published address instead.

#### Employee job-search demo

1. Start both servers and register/sign in as an employee; no job-provider account or key is needed.
2. Upload and confirm a resume, then optionally set preferred roles (for example, `Data Analyst, Business Intelligence Analyst`), location, experience, employment type, and a salary floor. Save the preferences.
3. Use the job-title/keyword search or sort menu; page through returned listings. Open the genuine application link and confirm the provider attribution.
4. Review the compatibility explanation, profile skills mentioned in the text, and other detected skills (which are not verified requirements). If no direct title match is on the page, use a related-role suggestion; saved location, salary, and employment filters remain unchanged.
5. Edit and save profile skills or job preferences to refresh recommendations. If a provider is unavailable, the dashboard reports it and retains genuine results from the other providers.

macOS/Linux example:

```bash
export FRONTEND_ORIGIN=http://localhost:5173
export SESSION_COOKIE_SECURE=false
python -m uvicorn main:app --reload
```

PowerShell example:

```powershell
$env:FRONTEND_ORIGIN = "http://localhost:5173"
$env:SESSION_COOKIE_SECURE = "false"
python -m uvicorn main:app --reload
```

For an HTTPS deployment, set `SESSION_COOKIE_SECURE=true`. The project is intentionally documented for local development and does not include deployment configuration.

## How the analysis works

For every skill required by the selected role:

```text
gap = max(0, required level - current level)
```

- A missing rating is calculated as level `0` but displayed as **Unassessed**.
- A recorded level `0` is still displayed as `0`, so it is distinguishable from missing data.
- Skills are sorted by the largest gap first.
- Skills met percentage is `(requirements met / total requirements) × 100`.
- Current levels `0–2` prioritize Beginner courses.
- Current levels `3–4` prioritize Intermediate and Advanced courses.
- A gap remains visible even when the local course catalog has no match.
- A skill mentioned in a resume has **no inferred proficiency**. Every extracted skill starts unrated. Employees choose `0–5` themselves; assessed employee ratings are labeled **Self-reported**.
- Employee analysis uses only the logged-in employee's saved profile. Missing and unrated required skills remain **Unassessed** and calculate as `0`.

## Employee demo walkthrough

1. Open the app and choose **Employee registration**. Enter a full name, a new username, and a password of at least eight characters. Registration signs you into your employee dashboard.
2. Upload `backend/sample_resumes/maya_shah.docx` or `maya_shah.pdf`. Both contain the same fictional resume. Review the extracted name, email, phone, education, work experience, and skills. Fields the parser cannot find display **Not detected** and can be entered manually.
3. Correct the email or name, remove a skill, and add a skill using the suggestions or a custom name. Set **SQL to 0** and **Python to 3**, leaving other skills **Unassessed**. The resume's `JS` mention is normalized to **JavaScript**.
4. Save the confirmed profile. Refresh the page and confirm the details and ratings persist. Select **Data Analyst** and run analysis. Python meets the requirement; SQL has a four-level gap. Statistics remains Unassessed. Course recommendations come from the existing local catalog.
5. Upload the other sample as a replacement. Its extracted details remain a draft until you save them. Cancel/discard the review to keep the earlier profile and ratings. A replacement does not silently inherit ratings for newly extracted skills.
6. Sign out and sign back in as your HR admin to demonstrate the original employee selector and sample analyses. A registered employee cannot call the HR employee list or HR analysis APIs.
7. Review the automatically loaded **Recommended Career Roles** based on the saved profile. Compare the compatibility estimate, matching and missing skills, recommendation reason, and essential/optional ESCO skills. Use the imported ESCO career-category filter or search a title, alternative title, or skill to find catalog profiles. Select **View Available Jobs** to search real current listings for that role. Career recommendations continue to work if the live job provider is unavailable.

### Resume parsing limits

- Accepted: text-based `.pdf` and `.docx`, up to **5 MiB (5 × 1024 × 1024 bytes)** per file. Old `.doc`, images, and other extensions are rejected.
- Corrupted, password-protected, and scanned PDFs without extractable text return a clear error. OCR is not included.
- Parsing uses straightforward patterns and section headings. Unusual layouts can produce incomplete details, which is why review and correction are required.
- Skills use the existing role/course vocabulary and a small alias map. Matching is evidence of a mention, not verification of skill or experience.
- Uploads are parsed locally and closed after parsing. The application keeps no original resume or raw-text archive. Only employee-confirmed fields and skill ratings are saved to SQLite; an unsaved draft is lost on page reload.
- The original HR sample-role catalog still has three roles for the HR analysis workflow. Career recommendations use only occupations in the imported ESCO release; sample HR roles and O*NET occupations are not returned as career recommendations.

### Career Role Recommendations demo walkthrough

1. Download/import ESCO v1.2.1 English CSV files and run embedding preparation once. Register three employees and save distinct confirmed skill sets: Python/SQL/Excel/Power BI; Java/Spring Boot/SQL; and Figma/Wireframing/Prototyping. No job title needs to be entered.
2. Open each employee dashboard. The **Recommended Career Roles** section automatically analyzes saved profile data against imported ESCO occupations.
3. Confirm that data skills surface analytics roles, Java skills surface software roles, and design skills surface design/interface roles. Compare matching and missing skills and the plain-language explanation. “Profile compatibility” is a local estimate, not a hiring prediction; a resume skill mention is not a proficiency assessment.
4. Confirm the recommendations appear independently of live vacancy availability. Select **View Available Jobs** on a role to search real listings through the existing job API; provider failures do not affect career recommendations.
5. Edit and save an employee's skills or career interests, then refresh the dashboard. Sign in as another employee and confirm recommendations use that account's profile.

## Two-minute HR demo walkthrough

1. **Sign in and introduce the dashboard (0:00–0:20).** Explain that the admin account was created locally and that all employee/course data is fictional CSV data.
2. **Matched course and unassessed skill (0:20–0:55).** Select **Aisha Patel** and **Data Analyst**, then click **Analyze skill gap**. Point out the recommended courses and the **Statistics — Unassessed** row. The missing rating calculates as zero without pretending it was an actual assessment.
3. **No matching course and recorded zero (0:55–1:20).** Select **Kabir Singh** and **Backend Developer**. Show **System Design** with “No matching course in the catalog.” Also show **Docker** at recorded level `0`; it is not labeled Unassessed.
4. **All requirements met (1:20–1:40).** Select **Vikram Rao** and **Data Analyst**. The dashboard shows `100%`, zero gaps, and the all-requirements-met success message.
5. **Changing selections changes the analysis (1:40–2:00).** Switch back to **Aisha Patel** while keeping **Data Analyst** selected and run the analysis again. Compare Aisha's `20%` result with Vikram's `100%`, then use **Sign out** to show session logout.

## API routes

| Method | Route | Authentication |
| --- | --- | --- |
| `POST` | `/api/auth/login` | Public, but requires the configured `Origin` |
| `POST` | `/api/auth/register` | Public employee registration; requires the configured `Origin` |
| `GET` | `/api/auth/me` | Valid session required |
| `POST` | `/api/auth/logout` | Clears the cookie and server session; requires the configured `Origin` |
| `GET` | `/api/employees` | HR admin only |
| `GET` | `/api/roles` | Valid session required |
| `GET` | `/api/skills` | Valid session required; vocabulary for skill suggestions |
| `GET` | `/api/analysis?employee_id=...&role_id=...` | HR admin only |
| `GET` | `/api/profile` | Employee's own confirmed profile |
| `PUT` | `/api/profile` | Employee's own profile; configured `Origin` required |
| `POST` | `/api/resume/parse` | Employee-only multipart `file`; configured `Origin` required |
| `GET` | `/api/my-analysis?role_id=...` | Employee's own saved ratings |
| `GET` | `/api/job-preferences` | Employee's own saved job preferences |
| `PUT` | `/api/job-preferences` | Saves only the signed-in employee's preferences |
| `GET` | `/api/jobs?query=...&page=1&per_page=10&sort_by=relevance` | Employee-only public job search, ranked against the session profile |
| `GET` | `/api/career-recommendations?query=...&category=...&limit=10` | Employee-only local ESCO recommendations/search; returns categories from the imported catalog |
| `GET` | `/api/matches?q=...&category=...&page=1&page_size=10` | Employee-only compatibility route to the same local ESCO recommender |
| `GET` | `/api/matching/roles?query=...&category_code=...&offset=0&limit=10` | Employee-only search of the legacy O*NET skill-gap catalog |
| `GET` | `/api/matching/roles/{occupation_code}` | Employee-only O*NET role details, skill gaps, and course recommendations for the session profile |

Employee endpoints obtain the account ID from the session, never from a supplied employee ID. Matching returns the number of catalog roles considered before filters, the filtered result count, and one page of ranked matches. Registration cannot assign an administrator role. A missing/expired session returns `401`; the wrong account role returns `403`.

## Run verification

From `backend/` with the virtual environment active:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -v
```

Tests use temporary SQLite databases, generated in-memory credentials, fixture ESCO data, and mocked public-provider responses; they do not depend on external provider availability. They cover ESCO CSV import, occupation and skill embedding caching, profile-specific data/software/design recommendations, alternate-title/skill search, empty search, job-provider-independent career recommendations, registration/session/logout, resume parsing, employee isolation, the original HR workflow, O*NET imports and skill-gap analysis, and public-job provider normalization, caching, partial failures, filters, and ranking. Your local database is never used by these tests.

From `frontend/`:

```bash
npm run build
```

To regenerate the fictional sample files, run `python sample_resumes/generate_samples.py` from `backend/`.

## Authentication notes

- `setup_admin.py` uses `getpass`, so passwords are not shown while typing.
- Passwords are hashed with Argon2 and never stored in plain text.
- Login tokens are cryptographically random. Only their SHA-256 hashes are stored in SQLite.
- The cookie is `HttpOnly`, `SameSite=Lax`, and configurable as `Secure` for HTTPS.
- Expiry is checked by the server. Expired and logged-out sessions are deleted.
- Login errors do not reveal whether a username exists.
- Repeated failed logins are limited in memory. This simple college-demo limiter resets when the backend process restarts.
- State-changing requests validate the exact configured frontend origin.

## Troubleshooting

- **Login says the request origin is not allowed:** open `http://localhost:5173`, or set `FRONTEND_ORIGIN` to the exact URL shown in your browser and restart the backend.
- **Login succeeds but immediately disappears:** confirm `SESSION_COOKIE_SECURE=false` for local HTTP.
- **Frontend cannot reach the API:** confirm Uvicorn is running on `http://127.0.0.1:8000`; Vite proxies relative `/api` requests there.
- **Job search returns `404` in Docker:** confirm the frontend proxy targets the backend service (for example, `VITE_API_PROXY_TARGET=http://backend:8000`), not `127.0.0.1` inside the frontend container.
- **Job search reports that providers are unavailable:** check backend network access and provider board availability. No API credentials are required; successful providers continue to return results when one provider fails.
- **Admin setup says an account already exists:** the project supports one administrator plus employee accounts. Continue using that administrator and do not rerun setup.
- **A protected route returns `401`:** sign in again; the session may be missing, expired, or invalidated by logout.

