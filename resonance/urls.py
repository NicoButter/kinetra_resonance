"""
URL configuration for resonance project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from resonance.media import range_media

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('tracks.urls')),
    path('api/', include('tracks.api_urls')),
]

if settings.DEBUG:
    media_prefix = settings.MEDIA_URL.lstrip('/')
    urlpatterns += [re_path(rf'^{media_prefix}(?P<path>.*)$', range_media, name='range-media')]
