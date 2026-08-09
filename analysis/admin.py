from django.contrib import admin
from .models import AnalysisArtifact, ReviewAction, ReviewSession

@admin.register(AnalysisArtifact)
class AnalysisArtifactAdmin(admin.ModelAdmin):
    list_display = ('track', 'processing_job', 'type', 'stage', 'version', 'stem', 'created_at')
    list_filter = ('stage', 'type')

@admin.register(ReviewSession)
class ReviewSessionAdmin(admin.ModelAdmin):
    list_display = ('processing_job', 'review_version', 'status', 'version', 'created_at', 'finished_at')
    list_filter = ('status',)

@admin.register(ReviewAction)
class ReviewActionAdmin(admin.ModelAdmin):
    list_display = ('review_session', 'sequence', 'channel', 'action_type', 'event_id', 'batch_id', 'created_at')
    list_filter = ('channel', 'action_type')

# Register your models here.
