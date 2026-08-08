import os

from django import forms
from django.conf import settings
from processing.models import ProcessingProfile


class ProcessingOptionsForm(forms.Form):
    profile = forms.ChoiceField(
        choices=ProcessingProfile.choices,
        initial=ProcessingProfile.TELEO_6_STEM,
        label='Processing profile',
        widget=forms.RadioSelect,
        help_text='Teleo separates vocals, drums, bass, guitar, piano and other instruments.',
    )
    separator_model = forms.CharField(max_length=255, required=False, label='Separation model override (optional)')


class TrackUploadForm(ProcessingOptionsForm):
    title = forms.CharField(max_length=255)
    artist = forms.CharField(max_length=255, required=False)
    source_file = forms.FileField(label='Audio file')


    field_order = ['title', 'artist', 'source_file', 'profile', 'separator_model']

    def clean_source_file(self):
        audio = self.cleaned_data['source_file']
        name = os.path.basename(audio.name)
        extension = os.path.splitext(name)[1].lower()
        if name != audio.name or extension not in {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg'}:
            raise forms.ValidationError('Upload a supported audio file (.mp3, .wav, .flac, .m4a, .aac, .ogg).')
        if not audio.size:
            raise forms.ValidationError('The uploaded file is empty.')
        if audio.size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise forms.ValidationError(f'Files must be at most {settings.MAX_UPLOAD_SIZE_MB} MB.')
        return audio


class ReprocessTrackForm(ProcessingOptionsForm):
    pass
