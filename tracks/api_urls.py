from django.urls import path
from . import views

urlpatterns = [
    path('jobs/<uuid:job_id>/status/', views.job_status, name='job-status'),
    path('tracks/', views.track_list_api, name='track-list-api'),
    path('tracks/<uuid:track_id>/', views.track_detail_api, name='track-detail-api'),
    path('tracks/<uuid:track_id>/stems/', views.stems_api, name='stems-api'),
    path('tracks/<uuid:track_id>/analysis/', views.analysis_api, name='analysis-api'),
]
