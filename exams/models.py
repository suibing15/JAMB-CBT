from django.db import models
from django.utils import timezone
from accounts.models import SUBJECT_CHOICES, Candidate


class ExamYear(models.Model):
    year = models.CharField(max_length=20, unique=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.year


class ExamSubject(models.Model):
    exam_year = models.ForeignKey(ExamYear, on_delete=models.CASCADE, related_name="subjects")
    subject = models.CharField(max_length=50, choices=SUBJECT_CHOICES)
    # NOTE: a per-subject `duration_minutes` field used to live here
    # from the old per-subject-timer engine. It's gone now — the
    # native JAMB engine uses ONE shared clock for the whole sitting
    # (see UTMESession.total_duration_minutes below), so a per-subject
    # duration has no meaning anymore. Removed rather than left as
    # unused/misleading dead weight (see migration 0007 in this app
    # for the field removal, on top of exams/0006 which introduced
    # UTMESession itself).
    questions_to_display = models.PositiveIntegerField(default=50)

    class Meta:
        unique_together = ('exam_year', 'subject')

    def __str__(self):
        return f"{self.subject} ({self.exam_year.year})"


class Question(models.Model):
    exam_subject = models.ForeignKey(ExamSubject, on_delete=models.CASCADE, related_name="questions")
    question_text = models.TextField()
    image = models.ImageField(upload_to="questions/", blank=True, null=True)

    option_a = models.CharField(max_length=400)
    option_b = models.CharField(max_length=400)
    option_c = models.CharField(max_length=400)
    option_d = models.CharField(max_length=400)

    correct_answer = models.CharField(max_length=1, choices=[
        ("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")
    ])
    mark = models.FloatField(default=1.0)

    def __str__(self):
        return f"{self.exam_subject} - {self.question_text[:60]}..."


# ============================================================
# ✅ NATIVE JAMB-STYLE UTME SESSION
# ------------------------------------------------------------
# Real JAMB does NOT let a candidate "submit" one subject and
# move to the next. All 4 subjects are loaded together, the
# candidate can freely switch between them, and there is ONE
# shared countdown (standard UTME = 2 hours 10 minutes) for the
# whole sitting. Submission happens once, at the end, for all
# subjects together (or automatically when time elapses).
# ============================================================

# Standard JAMB UTME total duration (minutes) — configurable via
# settings.JAMB_TOTAL_DURATION_MINUTES, defaults to the real value.
DEFAULT_UTME_DURATION_MINUTES = 130  # 2 hrs 10 mins


class UTMESession(models.Model):
    """
    One CBT sitting for a candidate covering ALL of their registered
    subjects at once, exactly like the real JAMB UTME.
    """
    candidate = models.OneToOneField(
        Candidate, on_delete=models.CASCADE, related_name="utme_session"
    )
    exam_year = models.ForeignKey(ExamYear, on_delete=models.CASCADE, null=True, blank=True)

    subjects = models.ManyToManyField(ExamSubject, related_name="utme_sessions")

    # answers stored as {"<question_id>": "A", ...} across ALL subjects
    answers = models.JSONField(default=dict, blank=True)

    # which question (global flat index within its subject) the
    # candidate is currently looking at, per subject:
    # {"<exam_subject_id>": <index>}
    positions = models.JSONField(default=dict, blank=True)

    # which subject tab is currently active
    active_subject_id = models.PositiveIntegerField(null=True, blank=True)

    # marked-for-review questions: {"<question_id>": true}
    flagged = models.JSONField(default=dict, blank=True)

    total_duration_minutes = models.PositiveIntegerField(
        default=DEFAULT_UTME_DURATION_MINUTES
    )

    score = models.FloatField(default=0)
    subject_scores = models.JSONField(default=dict, blank=True)  # {"Mathematics": 38.0, ...}
    completed = models.BooleanField(default=False)

    started_at = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.candidate.full_name} — UTME Sitting ({self.exam_year.year if self.exam_year else 'N/A'})"

    def remaining_time(self):
        """Seconds left in the WHOLE sitting (all subjects share one clock)."""
        total_seconds = self.total_duration_minutes * 60
        elapsed = (timezone.now() - self.started_at).total_seconds()
        return max(0, int(total_seconds - elapsed))

    def is_expired(self):
        return self.remaining_time() <= 0

    def set_answer(self, question_id, choice):
        self.answers[str(question_id)] = choice.upper()
        self.last_updated = timezone.now()
        self.save(update_fields=["answers", "last_updated"])

    def toggle_flag(self, question_id):
        key = str(question_id)
        if self.flagged.get(key):
            self.flagged.pop(key, None)
        else:
            self.flagged[key] = True
        self.save(update_fields=["flagged"])

    def set_position(self, subject_id, index):
        self.positions[str(subject_id)] = index
        self.active_subject_id = subject_id
        self.save(update_fields=["positions", "active_subject_id"])

    def grade(self):
        """Score every subject and the sitting overall. Idempotent."""
        subject_scores = {}
        total = 0.0
        for subject in self.subjects.all():
            subject_total = 0.0
            for q in subject.questions.all():
                chosen = self.answers.get(str(q.id))
                if chosen and chosen.upper() == q.correct_answer.upper():
                    subject_total += float(q.mark)
            subject_scores[subject.subject] = subject_total
            total += subject_total

        self.subject_scores = subject_scores
        self.score = total
        self.completed = True
        self.submitted_at = timezone.now()
        self.save(update_fields=["subject_scores", "score", "completed", "submitted_at"])
        return subject_scores, total


# Backward-compatible alias so any leftover imports of the old
# per-subject ExamSession name don't hard-crash. New code should
# use UTMESession instead. This will be removed in a future cleanup.
ExamSession = UTMESession
