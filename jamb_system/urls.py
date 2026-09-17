from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from accounts.views import home, register_candidate, registration_success, download_pdf

# ===========================
# 🔐 OBSCURED ADMIN URL
# ------------------------------------------------------------
# NOTE: the previous version of this file *defined*
# SECURE_ADMIN_URL but never actually used it — admin.site.urls
# was still mounted at the plain, guessable "admin/" path. That
# was a loophole; it is now actually applied below. This is
# "security by obscurity" only — it does not replace strong admin
# passwords, MFA, or the rate limiting already applied elsewhere.
# The path itself now lives in settings.ADMIN_URL_PATH (single
# source of truth — see settings.py) so it can never drift out of
# sync with anywhere else in the app that needs to link to it.
# ===========================

urlpatterns = [
    # 🔐 Actually-obscured admin route
    path(settings.ADMIN_URL_PATH, admin.site.urls),

    # ===========================
    #  PUBLIC ROUTES
    # ===========================
    path('', home, name="home"),
    path('register/', register_candidate, name="register"),
    path(
        'registration-success/<int:candidate_id>/',
        registration_success,
        name="registration_success"
    ),
    path(
        'download-pdf/<int:candidate_id>/',
        download_pdf,
        name="download_pdf"
    ),

    # ===========================
    #  EXAMS / PORTAL ROUTES
    # ===========================
    path('exams/', include('exam_portal.urls')),

    # ===========================
    #  MANAGEMENT DASHBOARD
    # ===========================
    path('management/', include('management.urls')),
]

# Media is only served by Django itself in local development.
# In production, media should be served by Supabase Storage / S3
# directly (see USE_SUPABASE_STORAGE in settings.py) — Django/
# gunicorn should never be responsible for streaming user uploads
# at scale.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Registered so Django routes django-ratelimit's Ratelimited
# exception (a PermissionDenied subclass) to a friendly page instead
# of a raw 403/traceback. Only takes effect when DEBUG=False.
handler403 = "jamb_system.views.ratelimited_view"
