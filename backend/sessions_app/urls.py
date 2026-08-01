from django.urls import path

from . import views

urlpatterns = [
    path("new/", views.new_session, name="session_new"),
    path("upload/", views.upload_session, name="session_upload"),
    path("<int:session_id>/", views.session_review, name="session_review"),
    path("<int:session_id>/status/", views.session_status, name="session_status"),
    path("<int:session_id>/retry/", views.session_retry, name="session_retry"),
    path("<int:session_id>/delete/", views.session_delete, name="session_delete"),
    path("<int:session_id>/tickets/", views.session_tickets_json, name="session_tickets"),
    path("<int:session_id>/approve-high/", views.approve_high_confidence, name="session_approve_high"),
    path("<int:session_id>/push/", views.session_push, name="session_push"),
]
