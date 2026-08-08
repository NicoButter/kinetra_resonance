from django.contrib import admin
from .models import AnalysisArtifact

@admin.register(AnalysisArtifact)
class AnalysisArtifactAdmin(admin.ModelAdmin):
    list_display = ('track', 'processing_job', 'type', 'stage', 'version', 'stem', 'created_at')
    list_filter = ('stage', 'type')

# Register your models here.
