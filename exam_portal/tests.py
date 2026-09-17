from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.cache import cache


class ChatThreadIsolationTests(TestCase):
    """
    Regression tests for a real vulnerability found and fixed in this
    app: fetch_messages used to trust a client-supplied `contact`
    query param to decide which thread to return, meaning anyone
    could read anyone else's support chat just by knowing (or
    guessing) their phone number. It's now scoped to a server-issued
    thread_token stored in the visitor's own session.
    """

    def setUp(self):
        cache.clear()  # avoid rate-limit counters leaking between tests

    def test_visitor_can_read_their_own_thread(self):
        visitor = Client()
        visitor.post("/exams/contact/send/", {
            "name": "Alice", "contact": "08011112222",
            "category": "support", "text": "hello, need help",
        })
        resp = visitor.get("/exams/contact/fetch/")
        messages = resp.json()["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["message"], "hello, need help")

    def test_spoofing_another_visitors_contact_reveals_nothing(self):
        victim = Client()
        victim.post("/exams/contact/send/", {
            "name": "Victim", "contact": "08033334444",
            "category": "support", "text": "private message",
        })

        attacker = Client()
        resp = attacker.get("/exams/contact/fetch/?contact=08033334444")
        self.assertEqual(resp.json()["messages"], [])

    def test_two_visitors_sharing_the_same_stated_contact_stay_isolated(self):
        """
        Even if two different browser sessions both claim the exact
        same phone number, they must not see each other's messages —
        thread isolation is per-session, not per-phone-number.
        """
        visitor_a = Client()
        visitor_a.post("/exams/contact/send/", {
            "name": "A", "contact": "08099998888",
            "category": "support", "text": "message from A",
        })

        visitor_b = Client()
        visitor_b.post("/exams/contact/send/", {
            "name": "B", "contact": "08099998888",
            "category": "support", "text": "message from B",
        })

        a_messages = visitor_a.get("/exams/contact/fetch/").json()["messages"]
        b_messages = visitor_b.get("/exams/contact/fetch/").json()["messages"]

        self.assertEqual([m["message"] for m in a_messages], ["message from A"])
        self.assertEqual([m["message"] for m in b_messages], ["message from B"])

    def test_admin_reply_reaches_the_correct_visitor_only(self):
        visitor1 = Client()
        visitor1.post("/exams/contact/send/", {
            "name": "V1", "contact": "08011110000",
            "category": "support", "text": "thread 1",
        })
        visitor2 = Client()
        visitor2.post("/exams/contact/send/", {
            "name": "V2", "contact": "08022220000",
            "category": "support", "text": "thread 2",
        })

        User.objects.create_superuser("chatadmin", "a@a.com", "AdminPass123!")
        admin_client = Client()
        admin_client.login(username="chatadmin", password="AdminPass123!")

        admin_client.post("/exams/contact/admin-send/", {
            "contact": "08011110000",
            "text": "reply meant only for V1",
        })

        v1_messages = visitor1.get("/exams/contact/fetch/").json()["messages"]
        v2_messages = visitor2.get("/exams/contact/fetch/").json()["messages"]

        self.assertTrue(any("reply meant only for V1" in m["message"] for m in v1_messages))
        self.assertFalse(any("reply meant only for V1" in m["message"] for m in v2_messages))

    def test_legacy_unauthenticated_dump_endpoints_are_gone(self):
        """
        chat_api and chat_api_send used to return EVERY message in
        the system to anyone, unauthenticated, and were referenced
        nowhere in the UI. They were removed entirely rather than
        patched, since nothing depended on them.
        """
        resp = self.client.get("/exams/chat-api/")
        self.assertEqual(resp.status_code, 404)

        resp = self.client.post("/exams/chat-api/send/", {"message": "x"})
        self.assertEqual(resp.status_code, 404)


class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_login_rate_limited_after_threshold(self):
        statuses = []
        for i in range(12):
            resp = self.client.post("/exams/", {"phone": f"080{i:08d}", "pin": "x"})
            statuses.append(resp.status_code)
        # First 10/min should be allowed through (200, even if login
        # itself fails due to wrong PIN); the rest should be blocked.
        self.assertIn(429, statuses)
