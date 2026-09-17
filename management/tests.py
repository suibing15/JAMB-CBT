from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache


class ManagementPermissionTests(TestCase):
    """
    Regression tests for the "no zip file rendered" bug: Bulk PDF
    download requires the Admin role, but the dashboard used to show
    the link to everyone and silently redirect non-Admins with zero
    explanation. Now the link is hidden from non-Admins, and a direct
    hit shows a clear permission message instead of a silent bounce.
    """

    def setUp(self):
        cache.clear()  # avoid rate-limit counters leaking between tests
        self.admin_user = User.objects.create_superuser(
            "admin", "admin@example.com", "AdminPass123!"
        )
        self.viewer_user = User.objects.create_user(
            "viewer", "viewer@example.com", "ViewerPass123!"
        )
        viewer_group, _ = Group.objects.get_or_create(name="Viewer")
        self.viewer_user.groups.add(viewer_group)

        img = SimpleUploadedFile("p.png", b"fakeimgbytes", content_type="image/png")
        self.client.post("/register/", {
            "full_name": "Bulk Test", "phone": "08055556666",
            "subject1": "English", "subject2": "Mathematics",
            "subject3": "Physics", "subject4": "Chemistry",
            "address": "x", "cbt_preference": "Phone", "passport": img,
        })

    def test_admin_sees_bulk_download_link_and_it_works(self):
        c = Client()
        c.login(username="admin", password="AdminPass123!")

        resp = c.get("/management/")
        self.assertIn(b"download-all-pdfs", resp.content)

        resp = c.get("/management/download-all-pdfs/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/zip")
        self.assertTrue(resp.content.startswith(b"PK"))

    def test_viewer_does_not_see_bulk_download_link(self):
        c = Client()
        c.login(username="viewer", password="ViewerPass123!")
        resp = c.get("/management/")
        self.assertNotIn(b"download-all-pdfs", resp.content)

    def test_viewer_hitting_bulk_download_directly_sees_clear_message(self):
        c = Client()
        c.login(username="viewer", password="ViewerPass123!")
        resp = c.get("/management/download-all-pdfs/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("permission", resp.content.decode())

    def test_obscured_admin_url_works_and_default_admin_path_404s(self):
        c = Client()
        c.login(username="admin", password="AdminPass123!")
        self.assertEqual(c.get("/secure-admin-4932/").status_code, 200)
        self.assertEqual(c.get("/admin/").status_code, 404)
