from django.urls import path
from . import views

urlpatterns = [
    path('jobs/<uuid:job_id>/status/', views.job_status, name='job-status'),
    path('tracks/', views.track_list_api, name='track-list-api'),
    path('tracks/<uuid:track_id>/', views.track_detail_api, name='track-detail-api'),
    path('tracks/<uuid:track_id>/stems/', views.stems_api, name='stems-api'),
    path('tracks/<uuid:track_id>/analysis/', views.analysis_api, name='analysis-api'),
    path('reviews/<uuid:session_id>/data/', views.review_data_api, name='review-data-api'),
    path('reviews/<uuid:session_id>/actions/', views.review_action_api, name='review-action-api'),
    path('reviews/<uuid:session_id>/actions/batch/', views.review_batch_api, name='review-batch-api'),
    path('reviews/<uuid:session_id>/undo/', views.review_undo_api, name='review-undo-api'),
    path('reviews/<uuid:session_id>/redo/', views.review_redo_api, name='review-redo-api'),
    path('reviews/<uuid:session_id>/summary/', views.review_summary_api, name='review-summary-api'),
    path('reviews/<uuid:session_id>/finish/', views.review_finish_api, name='review-finish-api'),
]
