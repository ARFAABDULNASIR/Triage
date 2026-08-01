import logging
import threading

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from integrations.llm_providers import any_llm_available
from sessions_app.document_utils import MAX_DOCUMENT_BYTES, is_allowed_document
from sessions_app.media_utils import is_allowed_media
from sessions_app.models import MeetingSession, parse_zoom_transcript
from sessions_app.tasks import process_meeting_session
from tickets.models import ExtractedTicket
from tickets.serializers import ticket_to_dict

logger = logging.getLogger(__name__)


def _get_session(request, session_id):
    return get_object_or_404(MeetingSession, pk=session_id, workspace=request.workspace)


def _new_session_context(extra=None):
    from pathlib import Path

    samples_dir = Path(settings.BASE_DIR).parent / "samples"
    sample_files = []
    if samples_dir.exists():
        sample_files = sorted(samples_dir.glob("*.txt"))
    context = {
        "sample_files": [f.stem.replace("_", " ").title() for f in sample_files],
        "llm_configured": any_llm_available(),
    }
    if extra:
        context.update(extra)
    return context


@login_required
def new_session(request):
    return render(
        request,
        "sessions/new.html",
        _new_session_context({"error": request.GET.get("error")}),
    )


@login_required
@require_POST
def upload_session(request):
    input_type = request.POST.get("input_type", "transcript")
    title = request.POST.get("title", "").strip() or "Untitled meeting"
    sample_name = request.POST.get("sample_transcript", "").strip()

    def _error(message, status=400):
        return render(request, "sessions/new.html", _new_session_context({"error": message}), status=status)

    if sample_name:
        return _upload_sample_transcript(request, sample_name, title)

    upload = None
    if input_type == MeetingSession.INPUT_TRANSCRIPT:
        raw = request.POST.get("transcript", "").strip()
        if not raw:
            return _error("Please paste a transcript before extracting.")
    else:
        upload = request.FILES.get("file")
        if not upload:
            return _error("Please choose a file before extracting.")
        if is_allowed_document(upload.name):
            input_type = MeetingSession.INPUT_DOCUMENT
            if upload.size > MAX_DOCUMENT_BYTES:
                return _error("Document exceeds the 25MB limit.")
        elif is_allowed_media(upload.name):
            input_type = MeetingSession.INPUT_AUDIO
            if upload.size > settings.MAX_MEDIA_UPLOAD_BYTES:
                return _error("Recording exceeds the 100MB limit.")
        else:
            return _error(
                "Unsupported file type. Use a document (.pdf, .docx, .txt), "
                "audio (.mp3, .wav, .m4a, …) or video (.mp4, .mov, .webm, …)."
            )

    session = MeetingSession.objects.create(
        workspace=request.workspace,
        uploaded_by=request.user,
        title=title,
        input_type=input_type,
        status=MeetingSession.STATUS_PROCESSING,
    )

    if input_type == MeetingSession.INPUT_TRANSCRIPT:
        session.raw_transcript = parse_zoom_transcript(request.POST.get("transcript", ""))
        session.save(update_fields=["raw_transcript"])
    elif input_type == MeetingSession.INPUT_DOCUMENT:
        session.transcript_file = upload
        session.save(update_fields=["transcript_file"])
    else:
        session.audio_file = upload
        session.save(update_fields=["audio_file"])

    _enqueue_processing(session.pk)
    return redirect("session_review", session_id=session.pk)


def _upload_sample_transcript(request, sample_name: str, title: str):
    from pathlib import Path

    from integrations.extraction_pipeline import process_extraction_results
    from sessions_app.heuristic_extraction import heuristic_extract

    samples_dir = Path(settings.BASE_DIR).parent / "samples"
    slug = sample_name.lower().replace(" ", "_")
    path = samples_dir / f"{slug}.txt"
    if not path.exists():
        for f in samples_dir.glob("*.txt"):
            if slug in f.stem.lower():
                path = f
                break
    if not path.exists():
        return render(request, "sessions/new.html", _new_session_context({"error": "Sample transcript not found."}), status=400)

    transcript = path.read_text(encoding="utf-8")
    items, meta = process_extraction_results(heuristic_extract(transcript), transcript)

    session = MeetingSession.objects.create(
        workspace=request.workspace,
        uploaded_by=request.user,
        title=title or f"Sample: {sample_name}",
        input_type=MeetingSession.INPUT_TRANSCRIPT,
        status=MeetingSession.STATUS_READY,
        raw_transcript=transcript,
        extraction_meta={**meta.to_dict(), "fallback_used": True, "sample": True},
    )

    from sessions_app.tasks import _save_extracted_tickets

    _save_extracted_tickets(session, items, session.extraction_meta)
    return redirect("session_review", session_id=session.pk)


def _enqueue_processing(session_id: int):
    if settings.CELERY_TASK_ALWAYS_EAGER:

        def run():
            try:
                process_meeting_session(session_id)
            except Exception:
                logger.exception("Background processing failed for session %s", session_id)

        threading.Thread(target=run, daemon=True).start()
    else:
        process_meeting_session.delay(session_id)


@login_required
@require_GET
def session_status(request, session_id):
    session = _get_session(request, session_id)
    return JsonResponse(
        {
            "status": session.status,
            "stage": session.processing_stage,
            "detail": session.processing_detail,
            "error": session.error_message,
        }
    )


@login_required
@require_POST
def session_delete(request, session_id):
    """Delete a meeting session along with its uploaded files and tickets."""
    session = _get_session(request, session_id)
    for file_field in (session.audio_file, session.transcript_file):
        if file_field:
            file_field.delete(save=False)
    session.delete()
    return redirect("dashboard")


@login_required
@require_POST
def session_retry(request, session_id):
    """Re-run the extraction pipeline on the stored input, no re-upload needed."""
    session = _get_session(request, session_id)
    has_input = bool(
        session.raw_transcript.strip() or session.transcript_file or session.audio_file
    )
    if not has_input:
        return redirect("session_new")
    session.status = MeetingSession.STATUS_PROCESSING
    session.error_message = ""
    session.processing_stage = ""
    session.processing_detail = ""
    session.save(update_fields=["status", "error_message", "processing_stage", "processing_detail"])
    _enqueue_processing(session.pk)
    return redirect("session_review", session_id=session.pk)


@login_required
def session_review(request, session_id):
    session = _get_session(request, session_id)
    all_tickets = session.extractedticket_set.all()
    tickets = all_tickets.exclude(status=ExtractedTicket.STATUS_DISCARDED)
    flagged = tickets.filter(grounded=False) | tickets.filter(extraction_confidence__lt=0.5)
    high_conf = tickets.filter(
        grounded=True, extraction_confidence__gte=0.85, status=ExtractedTicket.STATUS_PENDING
    )
    approved = tickets.filter(status=ExtractedTicket.STATUS_APPROVED)
    pending = tickets.filter(status=ExtractedTicket.STATUS_PENDING)

    push_summary = {
        "create": approved.filter(match_action=ExtractedTicket.MATCH_CREATE).count(),
        "update": approved.filter(match_action=ExtractedTicket.MATCH_UPDATE).count(),
        "link": approved.filter(match_action=ExtractedTicket.MATCH_LINK).count(),
        "skip": approved.filter(match_action=ExtractedTicket.MATCH_SKIP).count(),
    }

    return render(
        request,
        "sessions/review.html",
        {
            "session": session,
            "tickets": tickets,
            "flagged_tickets": flagged.distinct(),
            "high_conf_tickets": high_conf,
            "approved_count": approved.count(),
            "pending_count": pending.count(),
            "flagged_count": flagged.distinct().count(),
            "high_conf_count": high_conf.count(),
            "push_summary": push_summary,
            "has_transcript": bool(session.raw_transcript)
            and not session.raw_transcript.startswith("[Extracted"),
        },
    )


@login_required
@require_GET
def session_tickets_json(request, session_id):
    session = _get_session(request, session_id)
    tickets = session.extractedticket_set.all()
    return JsonResponse([ticket_to_dict(t) for t in tickets], safe=False)


@login_required
@require_POST
def approve_high_confidence(request, session_id):
    session = _get_session(request, session_id)
    updated = session.extractedticket_set.filter(
        status=ExtractedTicket.STATUS_PENDING,
        grounded=True,
        extraction_confidence__gte=0.85,
    ).update(status=ExtractedTicket.STATUS_APPROVED)
    return redirect("session_review", session_id=session.pk) if request.htmx else JsonResponse({"approved": updated})


@login_required
@require_POST
def session_push(request, session_id):
    from tickets.services import push_approved_tickets

    session = _get_session(request, session_id)
    result = push_approved_tickets(session)
    if result["pushed"] > 0 and result["failed"] == 0:
        session.status = MeetingSession.STATUS_DONE
        session.save(update_fields=["status"])
    if request.htmx:
        return render(request, "sessions/partials/push_result.html", {"result": result, "session": session})
    return JsonResponse(result)
