# clockify-mcp

MCP server for [Clockify](https://clockify.me) — file time entries and query reports from Claude Code, Cowork, or any MCP client. Includes a `/log-session` skill that turns the conversation you just had into a confirmed time entry.

- **`add_time_entry`** — file an entry with start, end, project, description.
- **`list_time_entries`** — your entries in a date range (single project filter, auto-paginated, totals included).
- **`report_summary`** — totals from the Reports API, grouped by PROJECT / TASK / USER / DAY / WEEK / MONTH / TAG / CLIENT. The right tool for *"how many hours did I log on project X between A and B?"*.
- **`report_detailed`** — raw entries across multiple projects/users, hydrated with names.
- **`list_projects`**, **`list_tasks`**, **`list_tags`**, **`list_workspaces`**, **`get_running_timer`**, **`stop_running_timer`**, **`update_time_entry`**, **`delete_time_entry`**, **`whoami`**.

Natural-language time inputs are accepted everywhere: `"today 09:00"`, `"yesterday 14:30"`, `"2h ago"`, `"now"`, plus full ISO-8601. The server converts to the user's IANA timezone (read from `GET /user`) before posting to Clockify.

## Two modes

| Mode | Audience | Auth | Use when |
|---|---|---|---|
| **stdio** (default) | Claude Code, Cowork, local CLIs | Your single API key (env var or config file) | You work alone on one machine. |
| **HTTP + OAuth** (`--http`) | Claude desktop app's *Add custom connector* dialog, anyone speaking the MCP streamable-HTTP protocol over the network | OAuth 2.1 + PKCE; each user pastes their own Clockify API key once at /authorize, then the access token carries it (Fernet-encrypted) | You want one hosted instance that your colleagues each connect to with their own Clockify account. |

The same Python codebase runs both. Read the **stdio** section if you want it on your own laptop only; the **HTTP + OAuth** section is at the bottom and walks you through Cloud Run.

## Install (stdio)

```bash
uv tool install --from . clockify-mcp     # or:  pip install -e .
```

## Configure

Get an API key from <https://app.clockify.me/user/preferences#advanced> → "Generate".

Pick one:

```bash
export CLOCKIFY_API_KEY=<your-key>
```

Or in `~/.config/clockify-mcp/config.toml` (macOS / Linux) or `%APPDATA%\clockify-mcp\config.toml` (Windows):

```toml
api_key = "<your-key>"
# Optional — only if you're on a Clockify regional shard:
# api_base = "https://euc1.api.clockify.me/api/v1"
# reports_api_base = "https://euc1.reports.api.clockify.me/v1"
# default_workspace_id = "..."
# timezone = "Europe/Zurich"            # overrides /user's timeZone
```

Verify:

```bash
clockify-mcp --check
# OK. Authenticated as <Your Name> <your@email>
#   user_id:              …
#   default_workspace_id: …
#   active_workspace_id:  …
#   timezone:             …
```

## Wire into Claude Code

Add the server to your Claude Code config:

```bash
claude mcp add clockify -- clockify-mcp
```

Or edit `~/.claude.json` directly:

```json
{
  "mcpServers": {
    "clockify": {
      "command": "clockify-mcp"
    }
  }
}
```

Restart Claude Code. Verify the tools are loaded:

```
> /mcp
```

You should see `clockify` listed with ~13 tools.

## Use it

```
> Log the last 90 minutes to project "<your project>" as "<what you did>".
> How many hours did I log on project "<your project>" between <date> and <date>?
> Show me everything I logged this week, broken down by project.
> Stop my running timer.
```

The `/log-session` skill is the dedicated end-of-session flow: it estimates duration from the conversation start, summarises what you did, asks for the project, then confirms before posting.

## Set your organisation's billing rules

The plugin's skills follow three universal rules: 15-min quantum, single entry ≤ 2h, never double-book the same project. Any **organisation-specific** rule (e.g. whether parallel work on different clients may overlap in time, which internal meetings to skip when reconciling your calendar) is *not* baked into the plugin — the skills look for it in your local memory.

If you have a billing convention, add it once to `~/.claude/CLAUDE.md`:

```markdown
## Clockify

<Your org's overlap policy: e.g. "parallel sessions for different clients
may overlap; same-client overlap is forbidden">

<Which internal meeting patterns to skip when scanning the calendar>
```

Claude Code auto-loads this into every session, and the skills pick it up. Each user maintains their own — nothing org-specific ships in the repo.

## Share with colleagues (via the Claude desktop app plugin marketplace)

Push this repo to GitHub. Colleagues then, in the Claude desktop app:

1. **Customize → +** under Personal plugins → **Create plugin → Add marketplace** → paste the GitHub `owner/repo` (e.g. `<your-org>/clockify-mcp`).
2. Open the new marketplace, click **Install** on `clockify-mcp`.
3. First time they ask Claude something Clockify-related, they'll be redirected to a Connect Clockify form — paste their personal Clockify API key (Clockify → Preferences → Advanced → Generate). Their key stays encrypted inside their own JWT; the hosted server doesn't store it.

That's it — connector, skills, and slash commands are all wired up by the install.

## Tool reference

| Tool | What it does | Notes |
|---|---|---|
| `whoami` | Validate the key. Return user id, default workspace, timezone. | First call on startup; result is memoised. |
| `list_workspaces` | All workspaces this user belongs to. | |
| `list_projects` | Projects in a workspace. | Cached for `cache_ttl_seconds` (default 5 min). Filter by `name_filter`. |
| `list_tasks` | Tasks inside a project. | |
| `list_tags` | Tags on a workspace. | Cached. |
| `list_time_entries` | The user's entries in a date range. | Single project filter; auto-paginates; returns total hours. |
| `add_time_entry` | Create an entry. | Times accept ISO-8601 or natural language. |
| `update_time_entry` | Edit fields on an entry. | If you pass only one of `start`/`end`, the other is read from the existing entry. |
| `delete_time_entry` | Delete an entry. | Returns `{status: "deleted", entry_id}`. |
| `get_running_timer` | Currently-running entry, or `None`. | |
| `stop_running_timer` | Stop the running entry at a given time. | `end` defaults to `"now"`. |
| `report_summary` | Aggregated totals via the Reports API. | Group by PROJECT / TASK / USER / DAY / WEEK / MONTH / TAG / CLIENT. `only_me=True` restricts to the authenticated user. |
| `report_detailed` | Hydrated entries via the Reports API. | Multi-project / multi-user; use when `list_time_entries` is too limited. |

## Time input formats

Every datetime arg accepts:

| Input | Interpreted as |
|---|---|
| `"2026-05-13T09:30:00Z"` | UTC literal |
| `"2026-05-13T11:30:00+02:00"` | Offset literal |
| `"2026-05-13T09:30:00"` | User's timezone (no offset) |
| `"2026-05-13"` | Midnight in user's timezone |
| `"today 09:30"`, `"yesterday 14:00"` | Day word + HH:MM in user's tz |
| `"09:30"` | Today at HH:MM in user's tz |
| `"2h ago"`, `"45m ago"` | Relative to now |
| `"now"` | Current time |

All values are normalised to UTC `Z` format before being sent to Clockify.

## HTTP + OAuth (for the Claude desktop app's custom connector)

The desktop app's *Add custom connector (BETA)* dialog needs a public HTTPS URL. This mode runs the same MCP server over HTTP with an OAuth 2.1 + PKCE provider in front, so each colleague pastes their own Clockify API key once and the server is stateless from then on.

### How it works

```
       ┌─ Claude desktop app ─────────────────────────────────┐
       │  "Add custom connector"  →  https://your-cloud-run/  │
       └──────────────────────────────────────────────────────┘
              │  1. Discovers /.well-known/oauth-* endpoints
              │  2. Dynamic Client Registration at /register
              │  3. Opens /authorize → user pastes Clockify API key
              │  4. /token exchange (with PKCE) → access_token (JWT)
              ▼
       ┌─ clockify-mcp on Cloud Run ───────────────────────────┐
       │  Bearer token middleware decodes the JWT,             │
       │  decrypts the embedded Clockify API key,              │
       │  installs it as the per-request state,                │
       │  then hands the request to the MCP server.            │
       └───────────────────────────────────────────────────────┘
```

No database. The Clockify API key is **Fernet-encrypted inside the JWT** with a server-side key; rotating that key revokes all tokens at once.

### Local smoke test

```bash
export JWT_SIGNING_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export ENCRYPTION_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export PUBLIC_URL=http://localhost:8765
clockify-mcp --http --port 8765 --public-url "$PUBLIC_URL"

# In another terminal:
curl http://localhost:8765/.well-known/oauth-authorization-server | jq
open "http://localhost:8765/authorize?response_type=code&redirect_uri=http://localhost/cb&code_challenge=x&code_challenge_method=plain"
```

### Deploy to Cloud Run (free tier)

Prereqs: a Google Cloud account, a project, and the `gcloud` CLI. Cloud Run's free tier (2M req/mo, 360k GB-s/mo) covers this workload at $0.

```bash
# 1. Set your project and pick a region close to your team.
PROJECT_ID=your-gcp-project-id
REGION=europe-west1
SERVICE=clockify-mcp
gcloud config set project "$PROJECT_ID"
gcloud config set run/region "$REGION"

# 2. Enable the APIs once.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# 3. Generate the two server secrets (32 bytes each).
JWT_SIGNING_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
ENCRYPTION_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 4. First deploy — PUBLIC_URL is a placeholder; we update it after we know the real URL.
gcloud run deploy "$SERVICE" \
  --source . \
  --allow-unauthenticated \
  --set-env-vars="JWT_SIGNING_KEY=$JWT_SIGNING_KEY,ENCRYPTION_KEY=$ENCRYPTION_KEY,PUBLIC_URL=https://placeholder"

# 5. Grab the URL and update PUBLIC_URL.
URL=$(gcloud run services describe "$SERVICE" --format='value(status.url)')
gcloud run services update "$SERVICE" --update-env-vars="PUBLIC_URL=$URL"
echo "Connector URL: $URL"
```

That `$URL` (e.g. `https://clockify-mcp-xyz-ew.a.run.app`) is what you paste into the *Remote MCP server URL* field of the connector dialog. The OAuth fields can stay empty — the server supports dynamic client registration.

### After first deploy

Paste the URL into the Claude desktop app's custom-connector dialog, click **Add**, then **Connect**. You'll see the *Connect Clockify* form; paste your API key and you're in. Your colleagues each do the same with their own keys.

To rotate all tokens (kick everyone out): `gcloud run services update clockify-mcp --update-env-vars="JWT_SIGNING_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"`.

## Development

```bash
uv sync --all-groups
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- src-layout under `src/clockify_mcp/`
- Tests in `tests/` (respx for HTTP mocking, MagicMock for tool-layer tests)
- `clockify_mcp.client.ClockifyClient` is the standalone HTTP wrapper — also usable outside the MCP server (for scripts, automation).

## License

MIT.
