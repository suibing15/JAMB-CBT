from django.db import models
import secrets


def _generate_thread_token():
    return secrets.token_urlsafe(24)


class ChatMessage(models.Model):
    """
    Lightweight chat messages for live conversation between visitor and admin.
    """

    CATEGORY_CHOICES = [
        ('support', 'Support'),
        ('inquiry', 'Inquiry'),
        ('complaint', 'Complaint'),
    ]

    candidate = models.ForeignKey(
        'accounts.Candidate',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Optional link to candidate (if known)."
    )

    visitor_name = models.CharField(max_length=120, blank=True)
    visitor_contact = models.CharField(max_length=120, blank=True)
    category = models.CharField(max_length=16, choices=CATEGORY_CHOICES, default='support')

    # SECURITY FIX: previously, anyone could read a whole chat thread
    # just by supplying any `?contact=<phone number>` in a GET
    # request to fetch_messages — no proof they were actually the
    # person who owns that phone number. This token is generated
    # server-side, stored in the visitor's own browser session
    # (never sent back by the client as a guessable value), and is
    # the ONLY thing the public-facing fetch/send endpoints trust
    # for "is this the same visitor who started this thread". Admin
    # views still read by contact (an intentional admin capability;
    # admins are authenticated staff).
    thread_token = models.CharField(
        max_length=48, db_index=True, default=_generate_thread_token
    )

    message = models.TextField()
    is_admin = models.BooleanField(default=False)
    seen = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['is_admin']),
            models.Index(fields=['seen']),
            models.Index(fields=['visitor_contact']),
            models.Index(fields=['thread_token']),
        ]

    def __str__(self):
        who = "Admin" if self.is_admin else (self.visitor_name or "Visitor")
        snippet = (self.message[:50] + '...') if len(self.message) > 50 else self.message
        return f"{who} ({self.get_category_display()}): {snippet}"

    @property
    def thread_id(self):
        """Group conversation by visitor contact (used by admin views)."""
        return self.visitor_contact
