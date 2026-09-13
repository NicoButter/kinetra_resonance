from django.contrib import admin
from .models import AnalysisArtifact, ReviewAction, ReviewSession, TeleoPublication

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

@admin.register(TeleoPublication)
class TeleoPublicationAdmin(admin.ModelAdmin):
    list_display = ('track', 'processing_job', 'experience_version', 'schema_version', 'quality', 'lyrics_included', 'exported_at')
    list_filter = ('quality', 'lyrics_included', 'schema_version')
    readonly_fields = ('source_hash', 'canonical_checksum', 'experience_checksum', 'created_at', 'exported_at')

# Register your models here.
