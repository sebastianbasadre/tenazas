# Job-search platform

Reviewed against code on **2026-10-28**.

The `job_search` agent discovers opportunities using Tenazas's configured SearXNG,
calculates compatibility from `user_profile.json`, and tracks applications in
`workspace/runtime/jobs/opportunities.json`.

`startup.sh` also starts `job_search_watcher.py` with a watchdog. Its interval is
`JOB_SEARCH_INTERVAL_SECONDS` (default 600 s). It can discover jobs, prepare
documents, and notify Telegram, so running it is not a side-effect-free test.

## Configuration

Complete `workspace/runtime/user_profile.json`, for example:

```json
{
  "city": "Buenos Aires",
  "country": "Argentina",
  "roles_target": ["Product Manager", "TAM", "DevOps"],
  "tools": ["Docker", "Ollama", "AWS"]
}
```

CV matching uses `qwen2.5:3b` in `agent_job_search.py`. Check that it is installed
at the configured Ollama endpoint. Search and matching depend on external services;
results do not guarantee eligibility or that a posting remains open.

## Data and documents

| Path | Contents |
|---|---|
| `workspace/runtime/jobs/opportunities.json` | Opportunity pipeline |
| `workspace/runtime/jobs/master_cv.txt` | Master CV |
| `workspace/runtime/jobs/cv_template.txt`, `cv_template.pdf` | CV template |
| `workspace/runtime/jobs/tailored/` | Documents prepared per opportunity |
| `.openclaw/state/agents/job_search_status.json` | Container aggregate state; local fallback uses `workspace/state/agents/` |

Functions `save_master_cv`, `save_cv_template`, `analyze_matches`, and
`prepare_tailored_cv` support document handling; they are not CLI subcommands.
Review content and personal data before sharing a CV.

## Usage

Supported Telegram examples retain their Spanish wording because they illustrate
existing user-command matching:

- `buscame trabajo`
- `buscar oportunidades de Product Manager remoto`
- `cómo van mis postulaciones`
- `tengo entrevistas pendientes`

CLI:

```powershell
python workspace/runtime/agent_job_search.py search "Product Manager remoto"
python workspace/runtime/agent_job_search.py status
python workspace/runtime/agent_job_search.py move ID applied --note "CV submitted"
```

Valid states: `detected`, `interested`, `applied`, `screening`, `interview`, `offer`,
`rejected`, `withdrawn`, and `archived`.

The platform does not submit applications automatically. Submission requires
human review of the posting, employer, and corresponding CV.
