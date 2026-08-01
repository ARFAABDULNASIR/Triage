from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.views import LoginView
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views import View
from django.views.decorators.http import require_POST

from integrations.linear_client import LinearClient

from .models import Workspace, WorkspaceMembership, WorkspaceSettings


class SignupView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return render(request, "accounts/signup.html", {"form": UserCreationForm()})

    def post(self, request):
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            workspace = Workspace.objects.create(name=f"{user.username}'s Workspace")
            WorkspaceMembership.objects.create(user=user, workspace=workspace, role="owner")
            WorkspaceSettings.objects.create(workspace=workspace, tracker_type="linear")
            login(request, user)
            return redirect("dashboard")
        return render(request, "accounts/signup.html", {"form": form})


class AppLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = AuthenticationForm
    redirect_authenticated_user = True


class SettingsView(View):
    def get(self, request):
        if not request.workspace:
            return redirect("login")
        settings_obj, _ = WorkspaceSettings.objects.get_or_create(
            workspace=request.workspace,
            defaults={"tracker_type": "linear"},
        )
        creds = settings_obj.get_credentials()
        teams = []
        if creds.get("api_key") and settings_obj.tracker_type == "linear":
            try:
                teams = LinearClient(creds["api_key"]).list_teams()
            except Exception:
                teams = []
        return render(
            request,
            "accounts/settings.html",
            {
                "settings": settings_obj,
                "linear_api_key": creds.get("api_key", ""),
                "linear_teams": teams,
                "jira_url": creds.get("site_url", ""),
                "jira_email": creds.get("email", ""),
                "jira_token": creds.get("api_token", ""),
            },
        )

    def post(self, request):
        if not request.workspace:
            return redirect("login")
        settings_obj, _ = WorkspaceSettings.objects.get_or_create(workspace=request.workspace)
        tracker_type = request.POST.get("tracker_type", "linear")
        settings_obj.tracker_type = tracker_type
        settings_obj.linear_team_id = request.POST.get("linear_team_id", "").strip()
        settings_obj.auto_approve_high_confidence = request.POST.get("auto_approve_high_confidence") == "on"

        if tracker_type == "linear":
            api_key = request.POST.get("linear_api_key", "").strip()
            settings_obj.set_credentials({"api_key": api_key})
            # Linear can't create issues without a team. If none was picked,
            # try to select one automatically so pushing works out of the box.
            if api_key and not settings_obj.linear_team_id:
                try:
                    teams = LinearClient(api_key).list_teams()
                except Exception:
                    teams = []
                if len(teams) == 1:
                    settings_obj.linear_team_id = teams[0]["id"]
                    messages.success(
                        request,
                        f"Settings saved. Your Linear team \"{teams[0]['name']}\" was selected automatically.",
                    )
                elif teams:
                    messages.warning(
                        request,
                        "Settings saved, but no Linear team is selected yet. "
                        "Pick one from the team list below and save again. Pushing tickets needs a team.",
                    )
                else:
                    messages.success(request, "Settings saved.")
            else:
                messages.success(request, "Settings saved.")
        else:
            settings_obj.set_credentials(
                {
                    "site_url": _normalize_site_url(request.POST.get("jira_url", "")),
                    "email": request.POST.get("jira_email", "").strip(),
                    "api_token": request.POST.get("jira_token", "").strip(),
                }
            )
            messages.success(request, "Settings saved.")
        settings_obj.save()
        return redirect("settings")


def _friendly_connection_error(exc: Exception, service: str) -> str:
    msg = str(exc)
    if "401" in msg or "Unauthorized" in msg or "Authentication required" in msg:
        if service == "linear":
            return (
                "Linear rejected this key. Personal API keys start with lin_api_. "
                "Create one under Linear > Settings > Security & access > Personal API keys."
            )
        return "Jira rejected these credentials. Check the email and API token."
    if "getaddrinfo" in msg or "NameResolution" in msg or "Connection" in msg or "Max retries" in msg:
        return f"Could not reach {service.title()}. Check the URL and your internet connection."
    return msg[:200]


@login_required
@require_POST
def test_linear_connection(request):
    if not request.workspace:
        return JsonResponse({"ok": False, "error": "Not authenticated"}, status=401)
    api_key = request.POST.get("linear_api_key", "").strip()
    if not api_key:
        settings_obj = WorkspaceSettings.objects.filter(workspace=request.workspace).first()
        if settings_obj:
            api_key = settings_obj.get_credentials().get("api_key", "")
    if not api_key:
        return JsonResponse({"ok": False, "error": "Paste your Linear API key first."})
    try:
        viewer = LinearClient(api_key).test_connection()
        teams = LinearClient(api_key).list_teams()
        return JsonResponse({"ok": True, "viewer": viewer.get("name"), "teams": teams})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": _friendly_connection_error(exc, "linear")})


def _normalize_site_url(url: str) -> str:
    url = url.strip()
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    return url.rstrip("/")


@login_required
@require_POST
def test_jira_connection(request):
    import requests
    from requests.auth import HTTPBasicAuth

    if not request.workspace:
        return JsonResponse({"ok": False, "error": "Not authenticated"}, status=401)
    site = _normalize_site_url(request.POST.get("jira_url", ""))
    email = request.POST.get("jira_email", "").strip()
    token = request.POST.get("jira_token", "").strip()
    if not all([site, email, token]):
        return JsonResponse({"ok": False, "error": "Fill in the site URL, email, and API token first."})
    try:
        resp = requests.get(
            f"https://{site}/rest/api/3/myself",
            auth=HTTPBasicAuth(email, token),
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code in (401, 403):
            return JsonResponse({
                "ok": False,
                "error": "Jira rejected these credentials. Create an API token at "
                         "id.atlassian.com > Security > API tokens, and use your account email.",
            })
        if resp.status_code == 404:
            return JsonResponse({
                "ok": False,
                "error": f"No Jira site found at {site}. Double-check the URL "
                         "(it usually looks like yourteam.atlassian.net).",
            })
        resp.raise_for_status()
        data = resp.json()
        return JsonResponse({"ok": True, "viewer": data.get("displayName") or data.get("emailAddress", "")})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": _friendly_connection_error(exc, "jira")})


def dashboard(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if not getattr(request, "workspace", None):
        return redirect("login")

    from django.db.models import Count

    from sessions_app.models import MeetingSession
    from tickets.models import ExtractedTicket

    sessions = (
        MeetingSession.objects.filter(workspace=request.workspace)
        .annotate(ticket_count=Count("extractedticket"))
        .order_by("-created_at")[:25]
    )
    tickets = ExtractedTicket.objects.filter(session__workspace=request.workspace)
    stats = {
        "meetings": MeetingSession.objects.filter(workspace=request.workspace).count(),
        "tickets": tickets.count(),
        "approved": tickets.filter(status=ExtractedTicket.STATUS_APPROVED).count(),
        "pushed": tickets.exclude(tracker_issue_key="").count(),
    }
    return render(request, "dashboard.html", {"sessions": sessions, "stats": stats})
