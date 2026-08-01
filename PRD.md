# PRD: Meeting → Tickets (AI Action Item Extractor)

**Status:** Draft v1
**Owner:** You
**Last updated:** June 29, 2026

---

## 1. One-line summary

A tool that takes a meeting recording or transcript, finds the action items using an LLM, lets a human approve or edit each one in a loop, and then pushes the approved ones to Linear or Jira.

---

## 2. Why build this

Meetings produce action items. Right now someone has to listen back, remember who said what, and manually type tickets into Linear or Jira. That's slow and stuff gets missed or misattributed.

The obvious shortcut — "just let AI write the tickets" — is also the obvious trap. An AI that quietly writes wrong tickets into your tracker is worse than no AI at all, because:

- A ticket assigned to the wrong person sits there ignored.
- A made-up ("hallucinated") action item adds noise nobody asked for.
- Once one bad ticket slips through, people stop trusting the tool and stop using it.

So the core idea of this project is not "AI extracts tickets." It's **"AI proposes, a human confirms, then it gets written."** That loop is the actual product. Everything else (transcription, parsing, the Jira/Linear API call) is plumbing around that loop.

---

## 3. Goals and non-goals

**Goals for MVP (buildable in a weekend):**
- Upload an audio file OR paste a transcript (Zoom/Meet auto-transcript text).
- Extract candidate action items as structured data, each one traceable to the exact sentence it came from.
- Show each candidate in a review loop: see it, edit it if needed, approve or discard it.
- Once the user is done reviewing, push all approved items in one batch to **either Linear or Jira** (user's choice, set once per workspace).

**Non-goals for MVP (explicitly skipped, can be v2):**
- Speaker diarization (knowing exactly who said what, word for word, by voice). Skip for audio uploads. Zoom/Meet transcripts already include speaker names as plain text, so that path is fine as-is.
- Supporting Linear AND Jira at the same time for one workspace.
- Real-time/live transcription during a call. This is a "after the meeting ends" tool, not a live assistant.
- Mobile app. Web only.

---

## 4. The core loop (this is the differentiator — read this section twice)

This is the part of the PRD to get right before writing any code, because it's the part that makes this a product instead of a toy.

### 4.1 Why a loop and not a straight line

A linear pipeline looks like: *audio → transcript → tickets → Jira.* That's fast to build and feels impressive in a demo. But it has no safety net. If step 3 makes something up, it's already live in your tracker by the time anyone notices.

A loop means: *propose → human looks at it → human decides → only then does anything leave the system.* The "loop" is really two nested checkpoints:

1. **Per-ticket checkpoint** — every single extracted item must be individually approved, edited, or discarded. Nothing is approved by default.
2. **Batch checkpoint** — even after individual approvals, nothing actually gets written to Linear/Jira until the user clicks one final "Push approved tickets" button. This is a second, deliberate gate.

Two gates instead of one. That redundancy is the point — it's what makes this trustworthy enough that someone would actually turn it on for their real meetings.

### 4.2 What the loop looks like on screen

Each extracted item is a card with three possible states: **Pending → Approved / Discarded**. The card shows:

- The **exact original sentence(s)** the item came from (so the reviewer doesn't have to trust the AI blindly — they can check it in two seconds).
- Editable fields: title, assignee, priority, description.
- Three buttons: **Discard**, **Edit**, **Approve**.

Edited-and-approved still counts as approved — editing isn't a fourth state, it's just unlocking the fields before you hit approve.

### 4.3 What "edit and re-extract" means (per your answer: support both)

When a user edits a card, give them two options:
- **Quick edit** — just change the text fields directly (fix a name, change the due date). No AI involved, instant.
- **Re-extract** — if the AI clearly misread the meeting (e.g. it merged two different action items into one, or attributed something to the wrong person), let the user select the relevant chunk of transcript again and re-run just that one extraction. This re-runs the LLM on a narrower, user-confirmed slice of text rather than the whole transcript, so it's fast and cheap.

Both paths land back at "pending," ready to be approved again.

### 4.4 Why this section matters for your portfolio write-up

This loop — not the Whisper call, not the Jira API call — is the part worth explaining when you talk about this project later. The pitch is simple: **AI tools that write to your project tracker without oversight erode trust fast. One hallucinated ticket assigned to the wrong person, and nobody uses the tool again.** The two-gate approval loop is the deliberate fix for that failure mode, and it's the kind of design decision that shows you're thinking about adoption, not just "can I make the API call work."

---

## 5. How it works, step by step

### Step 1 — Get the meeting into text

Two input paths:

**Path A: Audio upload**
User uploads an audio file. It gets transcribed using **NVIDIA's hosted Parakeet speech-to-text model** (via `build.nvidia.com`). This replaces Whisper because Whisper has no free tier at all — it's a flat $0.006/minute forever, no matter how little you use it. NVIDIA's hosted Parakeet API, by contrast, is free to call with just a free API key — no GPU, no Docker container, nothing to host yourself for the MVP.

What you get back is plain text, optionally with word-level timestamps (handy later for "this was said at 12:43" in the UI, same idea you had with Whisper's `verbose_json`).

What you give up by leaving Whisper: nothing important for this MVP. Diarization wasn't built into Whisper anyway, so that trade-off doesn't change.

**Path B: Paste transcript**
User pastes raw text from a Zoom or Google Meet auto-transcript. These already come with speaker names and timestamps as plain text — a simple regex pulls those apart before sending the text onward. No transcription step needed for this path at all, so it's actually the easier, more reliable path for MVP — consider steering new users toward it first.

### Step 2 — Find the action items (LLM extraction)

**Implementation note (June 2026):** The live app uses **Google Gemini** (`gemini-2.0-flash-lite`) with a multi-stage extraction pipeline (prefilter → extract → ground quotes → validate → dedupe → confidence score → Linear backlog match). The original PRD specified Claude; Gemini was chosen for the free tier during development.

This is the part most people get wrong by being too vague with their prompt. "Extract the action items" produces messy, inconsistent answers you can't put into a form.

Instead, ask for **structured JSON only**, with every field spelled out:

```
SYSTEM_PROMPT = """
You extract action items from meeting transcripts into structured JSON.
Return ONLY a JSON array. No commentary. No markdown fences.

Each item must have:
- "title": short imperative sentence (max 8 words)
- "description": 1-2 sentence context from the meeting
- "assignee": name mentioned, or null
- "due_date": ISO date if mentioned, or null
- "priority": "high" | "medium" | "low" — infer from urgency cues
- "source_quote": the exact sentence(s) that generated this item

If no action items exist, return [].
"""
```

Two settings to get right:
- **Temperature = 0.** You want the same transcript to produce the same tickets every time. This is extraction, not creative writing.
- **Model: Claude Sonnet 4.6**, called via the Anthropic API, for the structured-output step. It's the part of the pipeline doing the actual "thinking," so it's worth using a strong model here even though transcription is now free.

### Step 3 — The `source_quote` field is the safety net

Every extracted ticket carries the exact original sentence(s) it came from. This is what's shown right next to the editable fields in the approval card, so a reviewer can check "did the AI actually hear this, or did it make it up?" in about two seconds, without re-reading the whole transcript. This single field is most of why the tool is trustworthy — it turns "trust the AI" into "verify the AI," which is a much easier ask.

### Step 4 — The approval loop

Covered in detail in Section 4. Short version: every ticket sits in "pending" until a human approves, edits, or discards it. Approved tickets move to a staging list. Nothing leaves the system yet.

### Step 5 — Batch push to Linear or Jira

Once review is done, the user clicks one button: **"Push approved tickets (N)."** This is the second gate. Only at this point does anything get written externally.

**The user picks Linear or Jira once, per workspace, in settings.** Build both integrations, but only one is active per user/workspace at a time for MVP — don't build a "push to both" flow (you confirmed this isn't needed). Architecturally, treat "where tickets get pushed" as a single swappable interface so you're not duplicating logic — see Section 7.

---

## 6. Why a 30–90 second wait needs its own design (the async pipeline)

A 60-minute meeting takes real time to transcribe and process — roughly 30–90 seconds end to end. If your web request just sits there waiting for that to finish, two things go wrong: the browser tab might time out, and the user is staring at a spinner with zero information.

The fix is to make transcription + extraction an **asynchronous background job**, not something that happens inside the same request that handles the upload:

1. User uploads audio → request returns immediately with "Processing..." and a session ID.
2. A background worker (Celery, backed by Redis as the task queue) does the actual work: call NVIDIA's ASR API → get transcript → call Claude API → get structured tickets → save them to the database.
3. The frontend polls (or uses a websocket, if you want to go further) to check "is this session ready yet?" and shows the approval UI the moment it is.

This means the user always sees an honest "this is working" state instead of a spinner that might silently fail after 30 seconds. It's also just correct engineering — you don't want one slow transcription job blocking your whole web server from answering other requests.

---

## 7. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Django + DRF | Familiar, well-documented, fast to build a CRUD-heavy app like this |
| Transcription | **NVIDIA Parakeet (hosted, via build.nvidia.com)** | Free API key, no GPU or Docker needed, swaps in cleanly for Whisper |
| Extraction | Claude API (claude-sonnet-4-6) | Strong structured JSON output, which is the one place quality really matters |
| Task queue | Celery + Redis | Needed because transcription + extraction takes 30–90 seconds — can't block a web request that long |
| Frontend | HTMX or plain JS | No need for a heavy framework for an MVP review-loop UI |
| Ticket push | **Linear GraphQL API** or **Jira REST API** (`POST /rest/api/3/issue`), user picks one | Both are well documented; build behind one shared interface (see below) |
| Storage | S3 or Cloudflare R2 | Store uploaded audio files |

**One implementation note on Linear vs Jira:** since the user picks one at setup and you're not running both at once, write a small shared interface in your backend — something like a `TicketPusher` base class with `push(ticket)` — and have a `LinearPusher` and `JiraPusher` each implement it. The rest of your code (the "push approved tickets" button, the batch logic) calls the interface, not the specific service. That way adding a third tracker later, or letting a user switch from Jira to Linear, doesn't require touching your core approval logic at all.

---

## 8. Data model

```python
class MeetingSession(models.Model):
    title = models.CharField(max_length=200, blank=True)
    input_type = models.CharField(choices=[('audio', 'Audio'), ('transcript', 'Pasted Transcript')], max_length=20)
    status = models.CharField(
        choices=[('processing', 'Processing'), ('ready', 'Ready for Review'), ('done', 'Done')],
        default='processing', max_length=20
    )
    raw_transcript = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ExtractedTicket(models.Model):
    session = models.ForeignKey('MeetingSession', on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField()
    assignee = models.CharField(max_length=100, blank=True)
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=10)
    source_quote = models.TextField()
    status = models.CharField(
        choices=[('pending', 'Pending'), ('approved', 'Approved'), ('discarded', 'Discarded')],
        default='pending', max_length=20
    )
    tracker_issue_key = models.CharField(max_length=30, blank=True)  # set after push; works for both Linear and Jira IDs
    tracker_type = models.CharField(choices=[('linear', 'Linear'), ('jira', 'Jira')], max_length=10, blank=True)


class WorkspaceSettings(models.Model):
    tracker_type = models.CharField(choices=[('linear', 'Linear'), ('jira', 'Jira')], max_length=10)
    api_credentials = models.JSONField()  # store encrypted, never plain text
```

---

## 9. MVP scope, in one sentence

**Upload audio or paste a transcript → wait through a clear "processing" state → review each extracted ticket in the approval loop (approve / edit / discard, with source quote visible) → click one batch button to push approved tickets to Linear or Jira.**

The three things that make this non-trivial to build, in order of importance:
1. The two-gate approval loop (Section 4) — this is the actual product.
2. The async pipeline with Celery (Section 6) — needed because the job is slow.
3. The Linear/Jira swappable push integration (Section 7) — needed because you're supporting both but only one at a time.

---

## 10. What to highlight in your portfolio write-up

Keep this tight — three points, each with a clear "why," not just a feature list:

1. **The human-in-the-loop approval pattern, and why you chose it.**
   AI tools that write to your project tracker without oversight erode trust fast — one hallucinated ticket assigned to the wrong person, and nobody uses the tool again. Explain the two-gate design specifically: per-ticket approval, then a separate batch push. That's a deliberate trust-building decision, not just "I added a confirm button."

2. **The `source_quote` traceability field.**
   Every ticket is traceable back to the exact words that generated it. This is what turns the review step from "trust the AI" into "verify the AI in two seconds" — explain that distinction, since it's the actual reason the field matters, not just that it exists.

3. **The async pipeline using Celery.**
   Transcribing a 60-minute meeting takes 30–90 seconds. Explain that you deliberately built this as a background job so the user gets an honest "processing" state instead of a spinner that might time out or fail silently. This shows you thought about the real-world shape of the problem, not just the happy-path demo.

Together, these three points read as: *this person thinks about trust, verifiability, and reliability — not just "can I wire up an API call."* That's the signal worth landing in a portfolio write-up, for both a software engineering and a product-management audience.

---

## 11. Open questions for you to settle before/while building

- **Deployment target** — still undecided per our last conversation. Doesn't block starting the build, but settle it before you need to actually deploy (Render/Railway/Fly.io are all reasonable low-effort picks for a Django + Celery + Redis stack).
- **Auth** — single-user tool for your own use, or multi-user from day one? Affects whether `WorkspaceSettings` needs a user/org foreign key now or can be added later.
- **NVIDIA API rate limits on the free tier** — worth a quick check before you build around it for anything beyond personal/demo use, in case there's a request-per-day cap that matters at scale.
