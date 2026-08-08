from django.contrib import admin
from .models import ProcessingJob

@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = ('track', 'profile', 'separator_model', 'status', 'progress', 'created_at')
    list_filter = ('profile', 'status')

# Register your models here.
