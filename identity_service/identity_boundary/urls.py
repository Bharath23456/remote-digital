from django.urls import path

from identity_store.views import create_candidate, health, resolve_candidate


urlpatterns = [
    path("health", health),
    path("v1/candidates", create_candidate),
    path("v1/candidates/<uuid:identity_reference>", resolve_candidate),
]
