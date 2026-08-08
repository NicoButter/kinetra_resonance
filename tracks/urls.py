from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('tracks/new/', views.track_create, name='track-create'),
    path('tracks/<uuid:track_id>/', views.track_detail, name='track-detail'),
    path('tracks/<uuid:track_id>/reprocess/', views.track_reprocess, name='track-reprocess'),
    path('lab/', views.lab, name='lab'),
    path('lab/jobs/<uuid:job_id>/', views.job_lab, name='job-lab'),
]
