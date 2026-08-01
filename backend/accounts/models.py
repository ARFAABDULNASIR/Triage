import json
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


def _get_fernet():
    key = settings.CREDENTIALS_ENCRYPTION_KEY
    if not key:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError):
        logger.warning(
            "CREDENTIALS_ENCRYPTION_KEY is not a valid Fernet key — credentials will be "
            "stored unencrypted. Generate one with Fernet.generate_key()."
        )
        return None


class Workspace(models.Model):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class WorkspaceMembership(models.Model):
    ROLE_CHOICES = [("owner", "Owner"), ("member", "Member")]

    user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE)
    role = models.CharField(choices=ROLE_CHOICES, default="member", max_length=10)

    class Meta:
        unique_together = ("user", "workspace")

    def __str__(self):
        return f"{self.user.username} @ {self.workspace.name}"


class WorkspaceSettings(models.Model):
    TRACKER_CHOICES = [("linear", "Linear"), ("jira", "Jira")]

    workspace = models.OneToOneField(Workspace, on_delete=models.CASCADE)
    tracker_type = models.CharField(choices=TRACKER_CHOICES, max_length=10, default="linear")
    linear_team_id = models.CharField(max_length=50, blank=True)
    auto_approve_high_confidence = models.BooleanField(default=False)
    _api_credentials_encrypted = models.TextField(blank=True, db_column="api_credentials")

    def set_credentials(self, data: dict):
        fernet = _get_fernet()
        payload = json.dumps(data)
        if fernet:
            self._api_credentials_encrypted = fernet.encrypt(payload.encode()).decode()
        else:
            self._api_credentials_encrypted = payload

    def get_credentials(self) -> dict:
        if not self._api_credentials_encrypted:
            return {}
        fernet = _get_fernet()
        raw = self._api_credentials_encrypted
        if fernet:
            try:
                raw = fernet.decrypt(raw.encode()).decode()
            except InvalidToken:
                # Stored as plaintext before an encryption key was configured —
                # fall through and try to parse it directly.
                pass
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def __str__(self):
        return f"Settings for {self.workspace.name}"
