from django.db import models
from django.utils.crypto import get_random_string


# ========== CANDIDATE REGISTRATION CHOICES ==========
SUBJECT_CHOICES = [
    ("English", "Use of English"),
    ("Mathematics", "Mathematics"),
    ("Physics", "Physics"),
    ("Chemistry", "Chemistry"),
    ("Biology", "Biology"),
    ("Government", "Government"),
    ("Economics", "Economics"),
    ("Literature", "Literature"),
    ("Commerce", "Commerce"),
]

CBT_PREF_CHOICES = [
    ("Phone", "Take Test on Phone"),
    ("Center", "Practice at Training Center"),
]


# ✅ CANDIDATE MODEL
# ------------------------------------------------------------
# NOTE: this file used to also define duplicate ExamYear /
# ExamSubject / Question models that were never actually used —
# the real, live versions are in exams/models.py and are what
# exam_portal and management both import. Those dead duplicates
# have been removed (see migration 0007) to stop future
# confusion about which model is authoritative.
class Candidate(models.Model):
    passport = models.ImageField(upload_to="passports/")
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, unique=True)

    subject1 = models.CharField(max_length=50, choices=SUBJECT_CHOICES)
    subject2 = models.CharField(max_length=50, choices=SUBJECT_CHOICES)
    subject3 = models.CharField(max_length=50, choices=SUBJECT_CHOICES)
    subject4 = models.CharField(max_length=50, choices=SUBJECT_CHOICES)

    address = models.CharField(max_length=200)
    cbt_preference = models.CharField(max_length=20, choices=CBT_PREF_CHOICES)
    signature = models.ImageField(upload_to="signatures/", blank=True, null=True)
    date_registered = models.DateTimeField(auto_now_add=True)

    # ✅ Candidate Login PIN (auto-generated).
    # Widened from 6 to 8 digits (1,000,000 -> 100,000,000 possible
    # values) to raise the cost of brute-forcing a specific
    # candidate's PIN, on top of the per-phone lockout already
    # enforced in exam_portal/views.py::exam_login.
    pin = models.CharField(max_length=8, blank=True, null=True)

    # NOTE: the old `registration_form` FileField was removed —
    # registration PDFs are now generated on demand and streamed
    # straight to the browser (see accounts/views.py), never saved
    # to disk or the database. See migration 0005 for the drop.

    def save(self, *args, **kwargs):
        # Auto-generate PIN if empty
        if not self.pin:
            self.pin = get_random_string(8, allowed_chars='0123456789')
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name
