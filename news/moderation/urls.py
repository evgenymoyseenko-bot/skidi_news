from django.urls import path

from . import manual_publish, views

app_name = "moderation"

urlpatterns = [
    path("action/<str:token>/", views.moderation_action, name="action"),
    path("manual-publish/<str:token>/", manual_publish.manual_publish_form, name="manual_publish"),
]
