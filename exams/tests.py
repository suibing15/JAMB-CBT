from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from django.core.cache import cache

from accounts.models import Candidate
from .models import ExamYear, ExamSubject, Question, UTMESession


class UTMESessionTests(TestCase):
    """
    Tests for the native, all-subjects-at-once JAMB engine. This is
    the most important part of the app to keep covered, since a
    regression here directly breaks candidates' ability to sit exams.
    """

    def setUp(self):
        self.year = ExamYear.objects.create(year="2026", active=True)
        self.candidate = Candidate.objects.create(
            full_name="Jane Doe",
            phone="08011119999",
            subject1="English",
            subject2="Mathematics",
            subject3="Physics",
            subject4="Chemistry",
            address="Test address",
            cbt_preference="Phone",
        )

        self.subjects = {}
        for name in ["English", "Mathematics", "Physics", "Chemistry"]:
            subj = ExamSubject.objects.create(
                exam_year=self.year, subject=name,
                questions_to_display=3,
            )
            for i in range(3):
                Question.objects.create(
                    exam_subject=subj,
                    question_text=f"{name} question {i + 1}?",
                    option_a="A", option_b="B", option_c="C", option_d="D",
                    correct_answer="A", mark=1.0,
                )
            self.subjects[name] = subj

    def _make_session(self, total_minutes=130):
        session = UTMESession.objects.create(
            candidate=self.candidate,
            exam_year=self.year,
            total_duration_minutes=total_minutes,
        )
        session.subjects.set(self.subjects.values())
        return session

    def test_session_covers_all_four_subjects_at_once(self):
        """
        The core behavior change: one sitting, all subjects attached
        together — not one subject unlocked at a time.
        """
        session = self._make_session()
        self.assertEqual(session.subjects.count(), 4)

    def test_answering_one_subject_does_not_require_finishing_it_first(self):
        """
        Candidates must be able to switch subjects mid-exam without
        submitting the one they were on. There's no "lock" concept in
        the model at all — this test documents and protects that.
        """
        session = self._make_session()
        math_q = self.subjects["Mathematics"].questions.first()
        session.set_answer(math_q.id, "A")

        # Switching to Physics doesn't require Mathematics to be
        # marked complete anywhere — there IS no such concept.
        self.assertFalse(session.completed)
        self.assertIn(str(math_q.id), session.answers)

    def test_shared_clock_covers_the_whole_sitting_not_per_subject(self):
        session = self._make_session(total_minutes=130)
        remaining = session.remaining_time()
        # Should be close to 130 minutes in seconds, not 60 (the old
        # per-subject duration).
        self.assertGreater(remaining, 129 * 60)
        self.assertLessEqual(remaining, 130 * 60)

    def test_expired_session_reports_zero_remaining_time(self):
        session = self._make_session(total_minutes=130)
        session.started_at = timezone.now() - timedelta(minutes=200)
        session.save(update_fields=["started_at"])
        self.assertTrue(session.is_expired())
        self.assertEqual(session.remaining_time(), 0)

    def test_grade_scores_every_subject_in_one_pass(self):
        session = self._make_session()
        for name, subj in self.subjects.items():
            for q in subj.questions.all():
                # Answer correctly for English/Mathematics, wrong for
                # the rest, to prove per-subject scores differ.
                choice = "A" if name in ("English", "Mathematics") else "B"
                session.set_answer(q.id, choice)

        subject_scores, total = session.grade()

        self.assertTrue(session.completed)
        self.assertEqual(subject_scores["English"], 3.0)
        self.assertEqual(subject_scores["Mathematics"], 3.0)
        self.assertEqual(subject_scores["Physics"], 0.0)
        self.assertEqual(subject_scores["Chemistry"], 0.0)
        self.assertEqual(total, 6.0)

    def test_flagging_a_question_toggles_on_and_off(self):
        session = self._make_session()
        q = self.subjects["English"].questions.first()

        session.toggle_flag(q.id)
        self.assertIn(str(q.id), session.flagged)

        session.toggle_flag(q.id)
        self.assertNotIn(str(q.id), session.flagged)


class UTMEFullFlowTests(TestCase):
    """
    End-to-end HTTP-level test of the native JAMB flow: login, begin
    the sitting, switch between subjects without submitting any one
    of them, answer questions, then submit everything at once.
    """

    def setUp(self):
        # Rate-limit counters live in the cache, which Django does
        # NOT reset between tests (only the DB gets reset). Without
        # this, an earlier test that intentionally floods the login
        # endpoint (see exam_portal.tests.LoginRateLimitTests) can
        # leave this test's own login attempt blocked by a stale
        # counter from a previous test in the same run.
        cache.clear()

        self.year = ExamYear.objects.create(year="2026", active=True)
        self.candidate = Candidate.objects.create(
            full_name="Full Flow Candidate",
            phone="08022229999",
            subject1="English",
            subject2="Mathematics",
            subject3="Physics",
            subject4="Chemistry",
            address="Test address",
            cbt_preference="Phone",
        )
        for name in ["English", "Mathematics", "Physics", "Chemistry"]:
            subj = ExamSubject.objects.create(
                exam_year=self.year, subject=name,
                questions_to_display=2,
            )
            for i in range(2):
                Question.objects.create(
                    exam_subject=subj,
                    question_text=f"{name} Q{i + 1}?",
                    option_a="A", option_b="B", option_c="C", option_d="D",
                    correct_answer="A", mark=1.0,
                )

    def test_full_sitting_end_to_end(self):
        self.client.post("/exams/", {"phone": self.candidate.phone, "pin": self.candidate.pin})
        self.client.post("/exams/start/begin/")

        session = UTMESession.objects.get(candidate=self.candidate)
        self.assertEqual(session.subjects.count(), 4)

        # Answer one question in Mathematics, then switch straight to
        # Physics WITHOUT finishing Mathematics — this is the whole
        # point of the native engine.
        math_subject = ExamSubject.objects.get(subject="Mathematics", exam_year=self.year)
        math_q = math_subject.questions.first()
        resp = self.client.post(
            "/exams/utme/?subject=Mathematics",
            {"action": "answer", "question_id": math_q.id, "answer": "A", "subject": "Mathematics"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get("/exams/utme/?subject=Physics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Physics", resp.content)

        # Submit the whole sitting in one action.
        resp = self.client.post("/exams/utme/", {"action": "submit_all"}, follow=True)
        self.assertEqual(resp.status_code, 200)

        session.refresh_from_db()
        self.assertTrue(session.completed)
        self.assertEqual(session.score, 1.0)  # only the one Mathematics answer was correct
