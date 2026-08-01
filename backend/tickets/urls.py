from django.urls import path

from . import views

urlpatterns = [
    path("<int:ticket_id>/approve/", views.approve_ticket, name="ticket_approve"),
    path("<int:ticket_id>/discard/", views.discard_ticket, name="ticket_discard"),
    path("<int:ticket_id>/reextract/", views.reextract_ticket, name="ticket_reextract"),
    path("<int:ticket_id>/edit/", views.quick_edit_ticket, name="ticket_edit"),
]
