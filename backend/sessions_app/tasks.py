import logging
import os

from celery import shared_task
from django.utils.dateparse import parse_date

from integrations.llm_extraction import extract_tickets
from integrations.llm_providers import _friendly_error, _is_rate_limited
from integrations.ticket_matcher import match_tickets_for_session
from integrations.transcription import transcribe
from sessions_app.document_utils import extract_text_from_document
from sessions_app.models import MeetingSession
from tickets.models import ExtractedTicket

logger = logging.getLogger(__name__)


def _set_stage(session: MeetingSession, stage: str, detail: str = ""):
    session.processing_stage = stage
    session.processing_detail = detail
    session.save(update_fields=["processing_stage", "processing_detail"])


def _progress_writer(session: MeetingSession):
    def on_progress(msg: str):
        session.processing_detail = msg[:200]
        session.save(update_fields=["processing_detail"])

    return on_progress


def _save_extracted_tickets(session: MeetingSession, items: list[dict], meta: dict | None = None):
    ExtractedTicket.objects.filter(session=session).delete()
    if meta:
        session.extraction_meta = meta
        session.save(update_fields=["extraction_meta"])
    if not items:
        return
    for item in items:
        due_raw = item.get("due_date")
        due = parse_date(str(due_raw)[:10]) if due_raw else None
        ExtractedTicket.objects.create(
            session=session,
            title=(item.get("title") or "Untitled")[:200],
            description=item.get("description") or "",
            assignee=(item.get("assignee") or "") if item.get("assignee") else "",
            due_date=due if due else None,
            priority=item.get("priority") or "medium",
            source_quote=item.get("source_quote") or "",
            grounded=item.get("grounded", True),
            extraction_confidence=item.get("extraction_confidence", 0.5),
            confidence_factors=item.get("confidence_factors") or {},
            confidence_reason=item.get("confidence_reason", ""),
            commitment_type=item.get("commitment_type", "implied"),
            match_action=item.get("match_action", ExtractedTicket.MATCH_CREATE),
            matched_issue_id=item.get("matched_issue_id", ""),
            matched_issue_key=item.get("matched_issue_key", ""),
            match_confidence=item.get("match_confidence"),
            match_reason=item.get("match_reason", ""),
        )


def _finalize_session(session: MeetingSession, result, team_id: str | None = None, extra_meta: dict | None = None):
    items = list(result.items)
    if items and session.workspace:
        _set_stage(session, "matching", "Checking new items against your existing backlog…")
        try:
            items = match_tickets_for_session(items, session.workspace, team_id)
        except Exception as exc:
            logger.warning("Ticket matching failed: %s", exc)

    meta = result.meta.to_dict() if result.meta else {}
    if extra_meta:
        meta.update(extra_meta)
    _save_extracted_tickets(session, items, meta)

    from accounts.models import WorkspaceSettings

    ws_settings = WorkspaceSettings.objects.filter(workspace=session.workspace).first()
    if ws_settings and ws_settings.auto_approve_high_confidence:
        session.extractedticket_set.filter(
            grounded=True,
            extraction_confidence__gte=0.85,
            status=ExtractedTicket.STATUS_PENDING,
        ).update(status=ExtractedTicket.STATUS_APPROVED)

    session.status = MeetingSession.STATUS_READY
    session.error_message = ""
    session.processing_stage = ""
    session.processing_detail = ""
    session.save(update_fields=["status", "error_message", "processing_stage", "processing_detail"])


@shared_task(bind=True, max_retries=2)
def process_meeting_session(self, session_id: int):
    try:
        session = MeetingSession.objects.select_related("workspace").get(pk=session_id)
    except MeetingSession.DoesNotExist:
        return

    from accounts.models import WorkspaceSettings

    ws_settings = WorkspaceSettings.objects.filter(workspace=session.workspace).first()
    team_id = (ws_settings.linear_team_id if ws_settings else "") or session.linear_team_id

    try:
        transcript = session.raw_transcript
        extra_meta = {}
        on_progress = _progress_writer(session)

        if session.input_type == MeetingSession.INPUT_DOCUMENT and session.transcript_file:
            doc_path = session.transcript_file.path
            if not os.path.exists(doc_path):
                raise FileNotFoundError("Document file not found on disk")
            _set_stage(session, "reading", "Extracting text from the document…")
            transcript = extract_text_from_document(doc_path)
            session.raw_transcript = transcript
            session.save(update_fields=["raw_transcript"])

        elif session.input_type == MeetingSession.INPUT_AUDIO and session.audio_file and not transcript.strip():
            media_path = session.audio_file.path
            if not os.path.exists(media_path):
                raise FileNotFoundError("Media file not found on disk")
            _set_stage(session, "transcribing")
            transcript, engine = transcribe(media_path, on_progress)
            extra_meta["transcription_engine"] = engine
            session.raw_transcript = transcript
            session.save(update_fields=["raw_transcript"])

        if not transcript.strip():
            raise ValueError("No transcript text available")

        _set_stage(session, "extracting")
        result = extract_tickets(transcript, on_progress)
        if not result.items:
            raise ValueError(
                "No action items found. The meeting may have no explicit commitments, "
                "or the AI may be rate-limited. Try again in a few minutes."
            )

        _finalize_session(session, result, team_id, extra_meta)

    except Exception as exc:
        logger.exception("Processing failed for session %s", session_id)
        if _is_rate_limited(exc) and self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=30)
        session.status = MeetingSession.STATUS_FAILED
        session.error_message = _friendly_error(exc)
        session.processing_stage = ""
        session.processing_detail = ""
        session.save(update_fields=["status", "error_message", "processing_stage", "processing_detail"])


@shared_task
def reextract_ticket_slice(session_id: int, ticket_id: int, slice_text: str):
    try:
        session = MeetingSession.objects.get(pk=session_id)
        ticket = ExtractedTicket.objects.get(pk=ticket_id, session=session)

        result = extract_tickets(slice_text)
        if not result.items:
            raise ValueError("No action items found in selected text")
        item = result.items[0]

        ticket.title = (item.get("title") or ticket.title)[:200]
        ticket.description = item.get("description") or ticket.description
        ticket.assignee = item.get("assignee") or ""
        due_raw = item.get("due_date")
        ticket.due_date = parse_date(str(due_raw)[:10]) if due_raw else None
        ticket.priority = item.get("priority") or ticket.priority
        ticket.source_quote = item.get("source_quote") or slice_text[:500]
        ticket.grounded = item.get("grounded", True)
        ticket.extraction_confidence = item.get("extraction_confidence", 0.5)
        ticket.confidence_factors = item.get("confidence_factors") or {}
        ticket.confidence_reason = item.get("confidence_reason", "")
        ticket.commitment_type = item.get("commitment_type", "implied")
        ticket.status = ExtractedTicket.STATUS_PENDING
        ticket.save()
        return ticket.pk
    except Exception:
        logger.exception("Re-extract failed for ticket %s", ticket_id)
        raise
