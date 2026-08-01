from django.contrib import admin

from .models import ExtractedTicket


@admin.register(ExtractedTicket)
class ExtractedTicketAdmin(admin.ModelAdmin):
    list_display = ("title", "session", "status", "priority", "assignee")
    list_filter = ("status", "priority")
