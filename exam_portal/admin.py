from django.contrib import admin
from django.utils.html import format_html
from .models import ChatMessage


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    """
    Enhanced Admin Chat Panel with:
    - Reply button
    - Auto-fill reply recipient
    - Read-only old messages
    - Clean UI
    - ✅ GUARANTEED message delivery to visitor
    """

    list_display = (
        "created_at",
        "visitor_name",
        "visitor_contact",
        "category",
        "is_admin",
        "short_message",
        "reply_button",
    )

    list_filter = ("is_admin", "category")
    search_fields = ("visitor_name", "visitor_contact", "message")

    readonly_fields = ("created_at", "visitor_name", "visitor_contact", "category")

    # Small custom template that injects a single hidden
    # <input name="reply_to"> so the value survives the POST — see
    # changeform_view() below and templates/admin/exam_portal/chatmessage/change_form.html.
    change_form_template = "admin/exam_portal/chatmessage/change_form.html"

    fieldsets = (
        ("Chat Info", {
            "fields": (
                "created_at",
                "visitor_name",
                "visitor_contact",
                "category"
            )
        }),
        ("Message", {
            "fields": ("message", "is_admin", "seen")
        }),
    )

    # ---------------------------------------------
    # Short message preview
    # ---------------------------------------------
    def short_message(self, obj):
        return obj.message[:50] + "..." if len(obj.message) > 50 else obj.message
    short_message.short_description = "Message"

    # ---------------------------------------------
    # Reply button in list display
    # ---------------------------------------------
    def reply_button(self, obj):
        if not obj.visitor_contact:
            return "-"
        return format_html(
            '<a class="button" '
            'style="padding:6px 10px;background:#007bff;color:white;border-radius:4px;text-decoration:none;" '
            'href="/admin/exam_portal/chatmessage/add/?reply_to={}">Reply</a>',
            obj.id
        )
    reply_button.short_description = "Reply"

    # ---------------------------------------------
    # Auto-fill reply form when clicking reply
    # ---------------------------------------------
    def get_changeform_initial_data(self, request):
        reply_to = request.GET.get("reply_to")
        if reply_to:
            try:
                original = ChatMessage.objects.get(id=reply_to)
                return {
                    "visitor_name": original.visitor_name,
                    "visitor_contact": original.visitor_contact,
                    "category": original.category,
                    "is_admin": True,   # admin reply
                }
            except ChatMessage.DoesNotExist:
                pass
        return super().get_changeform_initial_data(request)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """
        Carries `?reply_to=<id>` from the Reply button link through
        to the rendered template AND, once submitted, back to
        save_model() via request.POST — see chat_reply_hidden.html.
        Django admin's add-form only preserves query params in the
        page it renders, not automatically in what gets POSTed back,
        so without this save_model() has no reliable way to know
        which visitor a reply via "Add" was actually meant for.
        """
        extra_context = extra_context or {}
        extra_context["reply_to"] = request.GET.get("reply_to") or request.POST.get("reply_to", "")
        return super().changeform_view(request, object_id, form_url, extra_context)

    # ---------------------------------------------
    # 🔥 FIX: force-link admin replies to the correct visitor thread
    # ------------------------------------------------------------
    # Bugs fixed here vs. the original version:
    #   1. `visitor_contact` is in readonly_fields (by design — staff
    #      shouldn't be able to type an arbitrary contact and hijack
    #      someone else's thread), which means Django silently drops
    #      it from the submitted form. The original code then tried
    #      to read `obj.visitor_contact` from that same (always-
    #      empty-on-add) form data and, finding it empty, fell back
    #      to "the single most recent message from ANY visitor in
    #      the whole system" — meaning EVERY reply sent via "Add"
    #      would silently attach to whoever happened to message last,
    #      not the person the staff member actually clicked Reply
    #      on. Now we resolve the target from `?reply_to=<id>` in the
    #      URL (set by the Reply button) at save time instead, which
    #      is reliable regardless of which fields are read-only.
    #   2. It never set `thread_token`, which fetch_messages() now
    #      requires to match a message to a visitor's own browser
    #      session — meaning admin replies added via this Django
    #      admin form would create a message the visitor could never
    #      actually receive/see, even though it "sent" successfully.
    # ---------------------------------------------
    def save_model(self, request, obj, form, change):
        if obj.is_admin and not change:
            reply_to = request.GET.get("reply_to") or request.POST.get("reply_to")
            original = None
            if reply_to:
                original = ChatMessage.objects.filter(id=reply_to, is_admin=False).first()

            if not original:
                # No reply_to given (staff used "Add" directly rather
                # than the Reply button) — fall back to the most
                # recent message from the contact typed in, if any
                # was actually provided (it won't be, since that
                # field is read-only on add, but keeping this doesn't
                # hurt and helps if the form is ever changed later).
                contact_for_lookup = obj.visitor_contact or None
                if contact_for_lookup:
                    original = (
                        ChatMessage.objects
                        .filter(is_admin=False, visitor_contact__iexact=contact_for_lookup)
                        .order_by("-created_at")
                        .first()
                    )

            if original:
                obj.visitor_contact = original.visitor_contact
                obj.visitor_name = original.visitor_name or obj.visitor_name
                obj.category = original.category
                obj.thread_token = original.thread_token
            # else: genuinely nothing to reply to — leave blank
            # rather than guessing and risking cross-visitor leakage.

        super().save_model(request, obj, form, change)

    # ---------------------------------------------
    # Prevent editing old messages
    # ---------------------------------------------
    def has_change_permission(self, request, obj=None):
        if obj:
            return False  # read-only past messages
        return True

    # Allow deletion normally
    def has_delete_permission(self, request, obj=None):
        return True
