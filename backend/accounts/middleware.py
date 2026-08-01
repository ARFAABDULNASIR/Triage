from accounts.models import WorkspaceMembership


class WorkspaceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.workspace = None
        if request.user.is_authenticated:
            membership = (
                WorkspaceMembership.objects.filter(user=request.user)
                .select_related("workspace")
                .first()
            )
            if membership:
                request.workspace = membership.workspace
        return self.get_response(request)
