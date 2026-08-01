from accounts.models import WorkspaceMembership


def workspace_context(request):
    workspace = getattr(request, "workspace", None)
    return {"current_workspace": workspace}


def get_user_workspace(user):
    membership = WorkspaceMembership.objects.filter(user=user).select_related("workspace").first()
    return membership.workspace if membership else None
