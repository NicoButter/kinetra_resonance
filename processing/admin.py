from django.contrib import admin
from .models import ProcessingJob

@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = ('track', 'status', 'progress', 'current_stage', 'created_at')
    list_filter = ('status',)

# Register your models here.
