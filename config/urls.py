"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
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
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

import apps.events.views
import apps.hub.views

urlpatterns = [
    path('', apps.events.views.home, name='home'),
    path('terms/', apps.hub.views.terms, name='terms'),
    path('privacy/', apps.hub.views.privacy, name='privacy'),
    path('admin/', admin.site.urls),
    path('owner/', include('apps.hub.urls')),
    path('accounts/', include('apps.accounts.urls')),
    path('organizers/', include('apps.organizers.urls')),
    path('payments/', include('apps.payments.urls')),
    path('events/', include('apps.events.urls')),
    path('leagues/', include('apps.leagues.urls')),
    path('championships/', include('apps.championships.urls')),
    path('communities/', include('apps.communities.urls')),
    path('all-star/', include('apps.allstars.urls')),
    path('messages/', include('apps.messaging.urls')),
]

if settings.DEBUG:
    # Serve only the uploaded event images in development, never the rest of MEDIA_ROOT.
    urlpatterns += static(
        settings.MEDIA_URL + 'event_images/',
        document_root=settings.MEDIA_ROOT / 'event_images',
    )
