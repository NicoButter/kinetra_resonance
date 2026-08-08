from django.contrib import admin
from .models import AnalysisArtifact

@admin.register(AnalysisArtifact)
class AnalysisArtifactAdmin(admin.ModelAdmin):
    list_display = ('track', 'type', 'version', 'stem', 'created_at')
    list_filter = ('type',)

# Register your models here.
