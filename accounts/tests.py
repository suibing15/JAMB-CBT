from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache

from .models import Candidate


class CandidateRegistrationTests(TestCase):
    def setUp(self):
        cache.clear()  # avoid rate-limit counters leaking between tests

    def _register(self, phone="08011112222"):
        img = SimpleUploadedFile("p.png", b"fakeimgbytes", content_type="image/png")
        return self.client.post("/register/", {
            "full_name": "Test Candidate",
            "phone": phone,
            "subject1": "English",
            "subject2": "Mathematics",
            "subject3": "Physics",
            "subject4": "Chemistry",
            "address": "123 Test Street",
            "cbt_preference": "Phone",
            "passport": img,
        })

    def test_registration_creates_candidate_with_auto_generated_pin(self):
        self._register()
        candidate = Candidate.objects.get(phone="08011112222")
        # PIN was widened from 6 to 8 digits to raise brute-force cost
        # (100x more possible values) — see accounts/models.py.
        self.assertEqual(len(candidate.pin), 8)
        self.assertTrue(candidate.pin.isdigit())

    def test_registration_pdf_streams_without_touching_disk(self):
        """
        Regression test for the disk-write PDF bug: registration_form
        used to be saved to MEDIA_ROOT and read back from disk. It's
        now built entirely in memory (see accounts/views.py). This
        confirms the download still works and returns real PDF bytes.
        """
        self._register()
        candidate = Candidate.objects.get(phone="08011112222")

        resp = self.client.get(f"/download-pdf/{candidate.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_registration_form_field_no_longer_exists_on_model(self):
        """
        Regression test: the old `registration_form` FileField was
        removed once PDFs stopped being persisted to disk. If someone
        re-adds it without updating the streaming views, this will
        catch the drift.
        """
        self.assertFalse(hasattr(Candidate, "registration_form"))
