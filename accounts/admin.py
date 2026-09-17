from django.contrib import admin
from .models import Candidate


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "cbt_preference", "date_registered")
    search_fields = ("full_name", "phone")
    list_filter = ("cbt_preference", "date_registered")
    readonly_fields = ("pin", "date_registered")
