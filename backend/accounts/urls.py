from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.AppLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("settings/test-linear/", views.test_linear_connection, name="test_linear"),
    path("settings/test-jira/", views.test_jira_connection, name="test_jira"),
]
