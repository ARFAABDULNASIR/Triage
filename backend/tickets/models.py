from django.db import models


class ExtractedTicket(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DISCARDED = "discarded"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DISCARDED, "Discarded"),
    ]

    PRIORITY_CHOICES = [("high", "High"), ("medium", "Medium"), ("low", "Low")]
    TRACKER_CHOICES = [("linear", "Linear"), ("jira", "Jira")]

    MATCH_CREATE = "create_new"
    MATCH_UPDATE = "update_existing"
    MATCH_LINK = "link_duplicate"
    MATCH_SKIP = "skip_done"
    MATCH_CHOICES = [
        (MATCH_CREATE, "Create new"),
        (MATCH_UPDATE, "Update existing"),
        (MATCH_LINK, "Link duplicate"),
        (MATCH_SKIP, "Skip (done)"),
    ]

    session = models.ForeignKey("sessions_app.MeetingSession", on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField()
    assignee = models.CharField(max_length=100, blank=True)
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="medium")
    source_quote = models.TextField()
    status = models.CharField(choices=STATUS_CHOICES, default=STATUS_PENDING, max_length=20)
    tracker_issue_key = models.CharField(max_length=30, blank=True)
    tracker_type = models.CharField(choices=TRACKER_CHOICES, max_length=10, blank=True)
    push_error = models.TextField(blank=True)

    grounded = models.BooleanField(default=True)
    extraction_confidence = models.FloatField(default=0.5)
    confidence_factors = models.JSONField(default=dict, blank=True)
    confidence_reason = models.TextField(blank=True)
    commitment_type = models.CharField(max_length=10, blank=True, default="implied")

    match_action = models.CharField(
        max_length=20, choices=MATCH_CHOICES, default=MATCH_CREATE, blank=True
    )
    matched_issue_id = models.CharField(max_length=50, blank=True)
    matched_issue_key = models.CharField(max_length=30, blank=True)
    match_confidence = models.FloatField(null=True, blank=True)
    match_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["extraction_confidence", "id"]

    def __str__(self):
        return self.title

    @property
    def confidence_percent(self) -> int:
        return int(self.extraction_confidence * 100)

    @property
    def confidence_tier(self) -> str:
        if not self.grounded or self.extraction_confidence < 0.5:
            return "low"
        if self.extraction_confidence >= 0.85:
            return "high"
        return "medium"

    @property
    def needs_review(self) -> bool:
        return not self.grounded or self.extraction_confidence < 0.5
