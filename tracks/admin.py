from django.contrib import admin
from .models import Stem, Track

@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ('title', 'artist', 'file_size', 'duration_ms', 'created_at')
    search_fields = ('title', 'artist', 'original_filename')

@admin.register(Stem)
class StemAdmin(admin.ModelAdmin):
    list_display = ('track', 'type', 'duration_ms', 'created_at')
    list_filter = ('type',)

# Register your models here.
