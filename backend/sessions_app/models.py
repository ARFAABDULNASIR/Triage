import re

from django.conf import settings
from django.db import models


class MeetingSession(models.Model):
    INPUT_AUDIO = "audio"
    INPUT_TRANSCRIPT = "transcript"
    INPUT_DOCUMENT = "document"
    INPUT_CHOICES = [
        (INPUT_TRANSCRIPT, "Pasted Transcript"),
        (INPUT_DOCUMENT, "Uploaded Document"),
        (INPUT_AUDIO, "Audio/Video"),
    ]

    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_DONE = "done"
    STATUS_CHOICES = [
        (STATUS_PROCESSING, "Processing"),
        (STATUS_READY, "Ready for Review"),
        (STATUS_FAILED, "Failed"),
        (STATUS_DONE, "Done"),
    ]

    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.CASCADE)
    uploaded_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=200, blank=True)
    input_type = models.CharField(choices=INPUT_CHOICES, max_length=20)
    status = models.CharField(choices=STATUS_CHOICES, default=STATUS_PROCESSING, max_length=20)
    # Current pipeline step while processing: reading | transcribing | extracting | matching
    processing_stage = models.CharField(max_length=20, blank=True)
    # Human-readable detail, e.g. "Groq rate-limited — switching to Gemini"
    processing_detail = models.CharField(max_length=200, blank=True)
    raw_transcript = models.TextField(blank=True)
    audio_file = models.FileField(upload_to="audio/", blank=True, null=True)
    transcript_file = models.FileField(upload_to="documents/", blank=True, null=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    linear_team_id = models.CharField(max_length=50, blank=True)
    project_key = models.CharField(max_length=50, blank=True)
    extraction_meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or f"Session {self.pk}"


def parse_zoom_transcript(text: str) -> str:
    """Normalize Zoom/Meet transcript formatting into plain readable text."""
    lines = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Common patterns: "00:12:34 Name: text" or "Name  0:12\nText"
        m = re.match(r"^(\d{1,2}:\d{2}(?::\d{2})?)\s+([^:]+):\s*(.+)$", line)
        if m:
            lines.append(f"[{m.group(1)}] {m.group(2).strip()}: {m.group(3)}")
        else:
            lines.append(line)
    return "\n".join(lines)
