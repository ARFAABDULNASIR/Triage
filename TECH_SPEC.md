# Technical Build Spec — Meeting → Tickets

**Companion to PRD.md. Read PRD.md first for *what* and *why*. This doc is *how*.**
**Multi-user from day one. Brand new repo.**

This doc exists so Cursor (or any AI coding agent) doesn't have to guess at folder names, env var names, request shapes, or build order. Paste this whole file into Cursor's context (or point it at the file) before asking it to scaffold anything.

---

## 1. Repo structure

Set this up first, before writing any feature code. One repo, two top-level apps.

```
meeting-to-tickets/
├── backend/
│   ├── manage.py
│   ├── config/                  # Django project settings
│   │   ├── settings.py
│   │   ├── celery.py
│   │   └── urls.py
│   ├── accounts/                 # users, auth, workspaces
│   │   ├── models.py
│   │   └── ...
│   ├── sessions/                  # MeetingSession, upload handling
│   │   ├── models.py
│   │   ├── views.py
│   │   └── tasks.py               # Celery tasks live here
│   ├── tickets/                   # ExtractedTicket, approval loop, push logic
│   │   ├── models.py
│   │   ├── views.py
│   │   └── pushers/
│   │       ├── base.py            # TicketPusher interface
│   │       ├── linear.py
│   │       └── jira.py
│   ├── integrations/               # thin wrapper clients
│   │   ├── nvidia_asr.py
│   │   └── claude_extraction.py
│   └── requirements.txt
├── frontend/                      # HTMX templates + light JS, served by Django
│   └── templates/
├── .env.example
├── docker-compose.yml             # Postgres + Redis + Django + Celery worker
└── README.md
```

**Build it in this order** (each step should run and be testable before moving to the next):
1. Django project + Postgres + accounts app (users, login, signup) — confirm you can log in.
2. Workspace model + settings page (pick Linear or Jira, paste credentials) — confirm you can save and reload it.
3. `sessions` app: upload form → `MeetingSession` row created → status shows "processing" (no real processing yet, just the state machine).
4. Wire up Celery + Redis, make a dummy task that flips status to "ready" after a `sleep(5)` — confirm the async flow works end to end before touching any real AI calls.
5. Swap the dummy task for the real one: NVIDIA ASR call → Claude extraction call → `ExtractedTicket` rows created.
6. Build the approval loop UI (the part that matters most — see PRD Section 4).
7. Build the two pushers (Linear, Jira) behind the shared interface, wire up the batch push button.

Don't try to do steps 5–7 in one sitting. Each is independently testable.

---

## 2. Environment variables

Put these in `.env.example` with placeholder values, never commit real secrets. Use these exact names so nothing gets mismatched between your code and your `.env` file.

```
# Django
DJANGO_SECRET_KEY=
DJANGO_DEBUG=True
DATABASE_URL=postgres://user:pass@localhost:5432/meetingtotickets

# Redis / Celery
REDIS_URL=redis://localhost:6379/0

# NVIDIA (build.nvidia.com — free API key, no card needed)
NVIDIA_API_KEY=
NVIDIA_ASR_MODEL=parakeet-tdt-0.6b-v2

# Anthropic (extraction step)
ANTHROPIC_API_KEY=

# Storage (S3 or Cloudflare R2 — same env var names work for either)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_STORAGE_BUCKET_NAME=
AWS_S3_ENDPOINT_URL=          # leave blank for AWS, set for R2

# Encryption key for storing Linear/Jira credentials at rest
CREDENTIALS_ENCRYPTION_KEY=
```

**Linear and Jira credentials are NOT global env vars** — since this is multi-user, each workspace stores its own, encrypted, in the `WorkspaceSettings.api_credentials` field (see Section 5). The env vars above are only for services *you* (the developer) are paying for or hosting — NVIDIA and Anthropic API keys can stay as your own keys for MVP, since you're the one footing that bill across all users initially. If you want per-user billing later, that's a v2 change, not something to build now.

---

## 3. Multi-user / workspace model — the part that changes from a single-user build

Since you're doing multi-user from day one, get this right before building anything else, because retrofitting auth into existing models later is painful.

```python
# accounts/models.py
class Workspace(models.Model):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

class WorkspaceMembership(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    role = models.CharField(choices=[('owner', 'Owner'), ('member', 'Member')], default='member', max_length=10)
```

Then **every** model from the original PRD data model gets a `workspace` foreign key, not a `user` foreign key:

```python
class MeetingSession(models.Model):
    workspace = models.ForeignKey('accounts.Workspace', on_delete=models.CASCADE)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    # ...rest unchanged from PRD

class WorkspaceSettings(models.Model):
    workspace = models.OneToOneField('accounts.Workspace', on_delete=models.CASCADE)
    tracker_type = models.CharField(choices=[('linear', 'Linear'), ('jira', 'Jira')], max_length=10)
    api_credentials = models.JSONField()  # encrypted before save, see below
```

**Why workspace and not user directly:** this lets you add team features later (multiple people reviewing the same meeting's tickets) without a second migration. One Linear/Jira connection per workspace, shared by everyone in it — matches how teams actually use these tools.

**Encrypting credentials:** don't store Linear/Jira API tokens as plain JSON. Use `cryptography`'s `Fernet` with the `CREDENTIALS_ENCRYPTION_KEY` env var, encrypt before save, decrypt only when making the actual API call. Tell Cursor explicitly: *"never log or print decrypted credentials, even in debug output."*

**Every view and Celery task must filter by workspace.** This is the multi-user bug that's easy to miss: a query like `ExtractedTicket.objects.all()` instead of `ExtractedTicket.objects.filter(session__workspace=request.workspace)` leaks one workspace's tickets into another's view. Tell Cursor to add a workspace filter as a non-negotiable rule on every queryset touching these models.

---

## 4. API contracts — exact shapes, so nothing gets guessed

### 4.1 NVIDIA ASR call (from `integrations/nvidia_asr.py`)

```python
import requests

def transcribe(audio_file_path: str) -> dict:
    """
    Calls NVIDIA's hosted Parakeet ASR via build.nvidia.com.
    Returns: {"text": str, "segments": [{"start": float, "end": float, "text": str}]}
    """
    with open(audio_file_path, "rb") as f:
        response = requests.post(
            "https://integrate.api.nvidia.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
            files={"file": f},
            data={"model": NVIDIA_ASR_MODEL, "language": "en-US"},
        )
    response.raise_for_status()
    return response.json()
```

**Before wiring this in for real:** check the current request/response format on `build.nvidia.com`'s docs page for the Parakeet model — hosted API endpoints and parameter names can shift, and this is exactly the kind of detail worth confirming against the live docs rather than trusting a written spec. Treat the function signature above (`transcribe(path) -> dict`) as the stable contract the rest of your app codes against — that's what matters for everything downstream — and adjust the internals once you've checked.

**25MB-equivalent file size handling:** even though NVIDIA's hosted endpoint may have different limits than Whisper's 25MB cap, build the same defensive habit — check file size before upload, and split long audio with `pydub` if needed. Confirm the actual limit when you check the docs above.

### 4.2 Claude extraction call (from `integrations/claude_extraction.py`)

```python
import anthropic

def extract_tickets(transcript_text: str) -> list[dict]:
    """
    Returns a list of dicts, each matching the ExtractedTicket fields:
    [{"title": str, "description": str, "assignee": str|None,
      "due_date": str|None, "priority": "high"|"medium"|"low", "source_quote": str}]
    """
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        temperature=0,
        system=SYSTEM_PROMPT,  # the prompt from PRD.md Section 5, Step 2
        messages=[{"role": "user", "content": transcript_text}],
    )
    text = response.content[0].text
    return json.loads(text)  # wrap in try/except — see error handling below
```

**Error handling Cursor needs to build in, not skip:**
- If `json.loads` fails (model added commentary despite instructions), strip markdown fences and retry the parse once before giving up.
- If the transcript is very long, check it against Claude's context window before sending — chunk by speaker turns or by time if needed, and run extraction per chunk, merging results.
- Always wrap the API call itself in try/except and surface a clear "extraction failed, you can retry" state to the user — don't let a Celery task fail silently.

### 4.3 Internal endpoints (frontend ↔ backend)

These are the contracts your own HTMX frontend talks to. Defining these now means Cursor builds the frontend and backend against the same shapes instead of improvising both sides separately.

| Endpoint | Method | Purpose | Returns |
|---|---|---|---|
| `/sessions/upload/` | POST | Upload audio or pasted transcript | `{"session_id": int, "status": "processing"}` |
| `/sessions/<id>/status/` | GET | Poll for processing status | `{"status": "processing"\|"ready"\|"failed"}` |
| `/sessions/<id>/tickets/` | GET | List extracted tickets for review | `[{ticket fields..., "status": "pending"}]` |
| `/tickets/<id>/approve/` | POST | Approve (optionally with edited fields in body) | `{"status": "approved"}` |
| `/tickets/<id>/discard/` | POST | Discard | `{"status": "discarded"}` |
| `/tickets/<id>/reextract/` | POST | Re-run extraction on a user-selected transcript slice | `{ticket fields..., "status": "pending"}` |
| `/sessions/<id>/push/` | POST | Batch push all approved tickets | `{"pushed": int, "failed": int, "results": [...]}` |

---

## 5. Linear and Jira — auth specifics

This is the part people get stuck on, so spell it out:

**Linear** uses a GraphQL API. The workspace owner generates a **Personal API Key** from Linear's own settings (Settings → API → Personal API keys) and pastes it into your app's settings page. No OAuth flow needed for a single-workspace MVP — store the key encrypted as shown in Section 3.

**Jira** uses REST and needs three things from the user: their **Atlassian site URL** (e.g. `yourteam.atlassian.net`), their **email**, and an **API token** generated from `id.atlassian.com/manage-profile/security/api-tokens`. Jira auth is HTTP Basic Auth using email + token, not a bearer token like Linear.

Build the settings page to ask for the right fields conditionally based on which tracker the user picks — don't show Jira's three fields when they've selected Linear, and vice versa.

```python
# tickets/pushers/base.py
class TicketPusher:
    def push(self, ticket: "ExtractedTicket") -> str:
        """Returns the external issue key/ID on success, raises on failure."""
        raise NotImplementedError
```

Both `LinearPusher` and `JiraPusher` implement this. The batch push view loops over approved tickets and calls `.push()` once per ticket, catching failures per-ticket so one bad push doesn't kill the whole batch — return a results list (Section 4.3) showing what succeeded and what didn't, so the user can retry just the failures.

---

## 6. What to tell Cursor explicitly, up front, in its own words

When you start the Cursor session, paste in PRD.md and this file, then add a short instruction like:

> "Build this in the step order from Section 1 of TECH_SPEC.md. This is multi-user — every model and query must be scoped to `workspace`, never global. Don't build the NVIDIA/Claude integration until the async Celery skeleton works with a dummy task first. Use the exact env var names from TECH_SPEC.md Section 2."

That one paragraph, plus the two docs, removes almost all the ambiguity Cursor would otherwise have to fill in by guessing.

---

## 7. Still open (carried over from PRD, now more concrete)

- **Deployment target** — once picked, add a `Procfile` or `render.yaml`/`fly.toml` accordingly. Doesn't block local dev.
- **NVIDIA free-tier rate limits** — confirm current limits on build.nvidia.com before assuming it'll handle real usage beyond your own testing, especially now that multiple users could be triggering transcriptions.
- **Workspace invites** — multi-user from day one covers *multiple people in one workspace*, but doesn't yet cover *how someone gets invited* into a workspace. Decide before building the signup flow: open signup with auto-created workspace per user (simplest), or invite-only.
