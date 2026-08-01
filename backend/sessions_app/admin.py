from django.contrib import admin

from .models import MeetingSession


@admin.register(MeetingSession)
class MeetingSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "workspace", "input_type", "status", "created_at")
    list_filter = ("status", "input_type")
