from django.urls import path

from . import views

app_name = "moderation"

urlpatterns = [
    path("action/<str:token>/", views.moderation_action, name="action"),
]
