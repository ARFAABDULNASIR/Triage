from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from sessions_app.tasks import reextract_ticket_slice
from tickets.models import ExtractedTicket
from tickets.serializers import ticket_to_dict


def _get_ticket(request, ticket_id):
    return get_object_or_404(
        ExtractedTicket,
        pk=ticket_id,
        session__workspace=request.workspace,
    )


def _apply_form_fields(ticket, request):
    ticket.title = request.POST.get("title", ticket.title)[:200]
    ticket.description = request.POST.get("description", ticket.description)
    ticket.assignee = request.POST.get("assignee", ticket.assignee)
    due = request.POST.get("due_date")
    ticket.due_date = due if due and due.strip() else None
    ticket.priority = request.POST.get("priority", ticket.priority)
    match_action = request.POST.get("match_action")
    if match_action in dict(ExtractedTicket.MATCH_CHOICES):
        ticket.match_action = match_action


@login_required
@require_POST
def approve_ticket(request, ticket_id):
    ticket = _get_ticket(request, ticket_id)
    _apply_form_fields(ticket, request)
    ticket.status = ExtractedTicket.STATUS_APPROVED
    ticket.save()

    if request.htmx:
        return render(request, "tickets/partials/card.html", {"ticket": ticket})
    return JsonResponse({"status": "approved"})


@login_required
@require_POST
def discard_ticket(request, ticket_id):
    ticket = _get_ticket(request, ticket_id)
    ticket.status = ExtractedTicket.STATUS_DISCARDED
    ticket.save(update_fields=["status"])

    if request.htmx:
        return render(request, "tickets/partials/card.html", {"ticket": ticket})
    return JsonResponse({"status": "discarded"})


@login_required
@require_POST
def reextract_ticket(request, ticket_id):
    ticket = _get_ticket(request, ticket_id)
    slice_text = request.POST.get("slice_text", "").strip()
    if not slice_text:
        return JsonResponse({"error": "No text selected"}, status=400)

    reextract_ticket_slice.delay(ticket.session_id, ticket.pk, slice_text)

    if request.htmx:
        ticket.status = ExtractedTicket.STATUS_PENDING
        return render(
            request,
            "tickets/partials/card.html",
            {"ticket": ticket, "reextracting": True},
        )
    return JsonResponse({"status": "pending", "message": "Re-extraction started"})


@login_required
@require_POST
def quick_edit_ticket(request, ticket_id):
    ticket = _get_ticket(request, ticket_id)
    _apply_form_fields(ticket, request)
    ticket.status = ExtractedTicket.STATUS_PENDING
    ticket.save()

    if request.htmx:
        return render(request, "tickets/partials/card.html", {"ticket": ticket})
    return JsonResponse(ticket_to_dict(ticket))
