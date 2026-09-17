from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.core.cache import cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.admin.views.decorators import staff_member_required

from django_ratelimit.decorators import ratelimit

from accounts.models import Candidate
from exams.models import ExamYear, ExamSubject, UTMESession
from exam_portal.models import ChatMessage

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader

import os
import qrcode
from io import BytesIO


# ============================================================
# 🔒 BRUTE-FORCE / LOCKOUT HELPERS (candidate PIN login)
# ------------------------------------------------------------
# django-ratelimit throttles by IP/rate window; this adds a
# per-phone-number lockout so an attacker can't just rotate
# proxies/IPs and keep guessing a candidate's 6-digit PIN.
#
# On top of that, IP_DISTINCT_PHONE_LIMIT below catches a different
# attack shape: someone trying a handful of guesses against MANY
# different phone numbers from the same IP, deliberately staying
# under the per-phone lockout threshold for each individual number.
# The per-phone lockout alone wouldn't catch that; this will.
# ============================================================
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60  # 15 minutes

# If a single IP makes failed login attempts against more than this
# many DISTINCT phone numbers within the window, that IP itself gets
# locked out — regardless of how few attempts it made per number.
IP_DISTINCT_PHONE_LIMIT = 15
IP_DISTINCT_PHONE_WINDOW_SECONDS = 30 * 60  # 30 minutes
IP_LOCKOUT_SECONDS = 30 * 60


def _login_attempts_key(phone):
    return f"login_attempts:{phone}"


def _is_locked_out(phone):
    return cache.get(_login_attempts_key(phone), 0) >= MAX_LOGIN_ATTEMPTS


def _register_failed_attempt(phone):
    key = _login_attempts_key(phone)
    attempts = cache.get(key, 0) + 1
    cache.set(key, attempts, LOGIN_LOCKOUT_SECONDS)
    return attempts


def _clear_login_attempts(phone):
    cache.delete(_login_attempts_key(phone))


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _ip_lockout_key(ip):
    return f"ip_locked:{ip}"


def _ip_distinct_phones_key(ip):
    return f"ip_distinct_phones:{ip}"


def _is_ip_locked_out(ip):
    return bool(cache.get(_ip_lockout_key(ip)))


def _register_failed_attempt_for_ip(ip, phone):
    """
    Tracks the SET of distinct phone numbers a given IP has tried
    and failed against recently. If that set grows past
    IP_DISTINCT_PHONE_LIMIT, the whole IP gets locked out — this is
    the anomaly-detection layer for a patient/distributed guesser
    who stays under the per-phone attempt threshold for any single
    number but is clearly grinding through many candidates.
    """
    key = _ip_distinct_phones_key(ip)
    seen_phones = cache.get(key, set())
    if not isinstance(seen_phones, set):
        seen_phones = set()
    seen_phones.add(phone)
    cache.set(key, seen_phones, IP_DISTINCT_PHONE_WINDOW_SECONDS)

    if len(seen_phones) > IP_DISTINCT_PHONE_LIMIT:
        cache.set(_ip_lockout_key(ip), True, IP_LOCKOUT_SECONDS)
        return True
    return False


# ============================================================
# CANDIDATE LOGIN  (rate-limited + per-phone lockout + IP anomaly guard)
# ============================================================
@ratelimit(key='ip', rate='10/m', block=True)
@ratelimit(key='post:phone', rate='8/5m', block=True)
def exam_login(request):
    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()
        pin = (request.POST.get("pin") or "").strip()
        client_ip = _client_ip(request)

        if not phone or not pin:
            messages.error(request, "Phone number and PIN are required.")
            return render(request, "exam_portal/exam_login.html")

        if _is_ip_locked_out(client_ip):
            messages.error(
                request,
                "Too many login attempts from this network. "
                "Please try again later or contact support."
            )
            return render(request, "exam_portal/exam_login.html")

        if _is_locked_out(phone):
            messages.error(
                request,
                "Too many failed attempts on this phone number. "
                "Please try again in 15 minutes or contact support."
            )
            return render(request, "exam_portal/exam_login.html")

        try:
            candidate = Candidate.objects.get(phone=phone, pin=pin)
            _clear_login_attempts(phone)
            # Rotate the session key on privilege change to defeat
            # session fixation attacks.
            request.session.cycle_key()
            request.session['candidate_id'] = candidate.id
            return redirect('exam_portal:exam_start')
        except Candidate.DoesNotExist:
            attempts = _register_failed_attempt(phone)
            _register_failed_attempt_for_ip(client_ip, phone)
            remaining = max(0, MAX_LOGIN_ATTEMPTS - attempts)
            if remaining > 0:
                messages.error(
                    request,
                    f"Invalid Phone Number or Exam PIN. {remaining} attempt(s) left."
                )
            else:
                messages.error(
                    request,
                    "Too many failed attempts. This phone number is locked for 15 minutes."
                )

    return render(request, "exam_portal/exam_login.html")


def _require_candidate(request):
    """Return the logged-in candidate or None (caller redirects)."""
    candidate_id = request.session.get('candidate_id')
    if not candidate_id:
        return None
    return Candidate.objects.filter(id=candidate_id).first()


# ============================================================
# ✅ NATIVE JAMB-STYLE UTME START SCREEN
# ------------------------------------------------------------
# Real JAMB shows all subjects up front and lets the candidate
# freely switch between them under ONE shared timer. No more
# "finish one subject before you can start the next".
# ============================================================
def exam_start(request):
    candidate = _require_candidate(request)
    if not candidate:
        return redirect('exam_portal:exam_login')

    exam_year = ExamYear.objects.filter(active=True).first()
    if not exam_year:
        messages.error(request, "No active exam year has been configured yet.")
        return redirect('exam_portal:exam_login')

    chosen_subjects = [
        candidate.subject1, candidate.subject2,
        candidate.subject3, candidate.subject4,
    ]
    chosen_subjects = [s for s in chosen_subjects if s]

    subjects_qs = ExamSubject.objects.filter(
        exam_year=exam_year,
        subject__in=chosen_subjects,
    )

    missing = [
        s for s in chosen_subjects
        if not subjects_qs.filter(subject__iexact=s).exists()
    ]

    session = getattr(candidate, "utme_session", None)

    if session and session.completed:
        return redirect('exam_portal:view_result')

    return render(request, "exam_portal/exam_start.html", {
        "candidate": candidate,
        "subjects": subjects_qs,
        "missing_subjects": missing,
        "exam_year": exam_year,
        "in_progress": session is not None,
        "total_minutes": getattr(
            session, "total_duration_minutes",
            getattr(settings, "JAMB_TOTAL_DURATION_MINUTES", 130)
        ),
    })


@require_POST
def begin_utme(request):
    """Create (or resume) the single all-subjects UTME sitting."""
    candidate = _require_candidate(request)
    if not candidate:
        return redirect('exam_portal:exam_login')

    exam_year = ExamYear.objects.filter(active=True).first()
    if not exam_year:
        messages.error(request, "No active exam year configured.")
        return redirect('exam_portal:exam_start')

    chosen_subjects = [
        s for s in [candidate.subject1, candidate.subject2,
                    candidate.subject3, candidate.subject4]
        if s
    ]
    subjects = list(
        ExamSubject.objects.filter(exam_year=exam_year, subject__in=chosen_subjects)
    )

    if not subjects:
        messages.error(request, "No questions are available yet for your registered subjects.")
        return redirect('exam_portal:exam_start')

    session, created = UTMESession.objects.get_or_create(
        candidate=candidate,
        defaults={
            "exam_year": exam_year,
            "total_duration_minutes": getattr(
                settings, "JAMB_TOTAL_DURATION_MINUTES", 130
            ),
        },
    )
    if created:
        session.subjects.set(subjects)
        session.active_subject_id = subjects[0].id
        session.save(update_fields=["active_subject_id"])

    if session.completed:
        return redirect('exam_portal:view_result')

    if session.is_expired():
        session.grade()
        return redirect('exam_portal:view_result')

    return redirect('exam_portal:utme_exam')


# ============================================================
# ✅ UNIFIED UTME EXAM SCREEN — all subjects, free switching,
#    ONE shared countdown. This replaces the old
#    start_subject_exam per-subject flow.
# ============================================================
def utme_exam(request):
    candidate = _require_candidate(request)
    if not candidate:
        return redirect('exam_portal:exam_login')

    session = getattr(candidate, "utme_session", None)
    if not session:
        return redirect('exam_portal:exam_start')

    if session.completed:
        return redirect('exam_portal:view_result')

    if session.is_expired():
        session.grade()
        return redirect('exam_portal:view_result')

    subjects = list(session.subjects.all().order_by('id'))
    if not subjects:
        messages.error(request, "No subjects attached to this sitting.")
        return redirect('exam_portal:exam_start')

    # ---- Which subject tab is active? ----
    subject_param = request.GET.get('subject') or request.POST.get('subject')
    if subject_param:
        active_subject = next(
            (s for s in subjects if s.subject.lower() == subject_param.lower()),
            subjects[0]
        )
    else:
        active_subject = next(
            (s for s in subjects if s.id == session.active_subject_id),
            subjects[0]
        )

    questions = list(active_subject.questions.all().order_by('id')[:active_subject.questions_to_display])
    total_questions = len(questions)

    # ---- Handle answer submit (AJAX-friendly, also works as plain POST) ----
    if request.method == "POST" and request.POST.get("action") == "answer":
        q_id = request.POST.get("question_id")
        choice = (request.POST.get("answer") or "").upper()
        if q_id and choice in ("A", "B", "C", "D"):
            session.set_answer(q_id, choice)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "remaining": session.remaining_time()})

    if request.method == "POST" and request.POST.get("action") == "flag":
        q_id = request.POST.get("question_id")
        if q_id:
            session.toggle_flag(q_id)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True})

    # ---- Which question index within the active subject? ----
    q_param = request.GET.get('q')
    if q_param is not None:
        try:
            idx = int(q_param)
        except ValueError:
            idx = 0
    else:
        idx = session.positions.get(str(active_subject.id), 0)

    idx = max(0, min(idx, max(total_questions - 1, 0)))
    session.set_position(active_subject.id, idx)

    current_question = questions[idx] if questions else None

    # ---- Final submit (all subjects, one action — like real JAMB) ----
    if request.method == "POST" and request.POST.get("action") == "submit_all":
        session.grade()
        return redirect('exam_portal:view_result')

    # ---- Build subject summary for the tab bar (answered counts etc.) ----
    subject_summary = []
    for s in subjects:
        s_questions = list(s.questions.all().order_by('id')[:s.questions_to_display])
        answered = sum(1 for q in s_questions if str(q.id) in session.answers)
        subject_summary.append({
            "id": s.id,
            "name": s.subject,
            "total": len(s_questions),
            "answered": answered,
            "active": s.id == active_subject.id,
        })

    return render(request, "exam_portal/utme_exam.html", {
        "candidate": candidate,
        "session": session,
        "subjects": subject_summary,
        "active_subject": active_subject,
        "questions": questions,
        "current_question": current_question,
        "index": idx,
        "total_questions": total_questions,
        "remaining": session.remaining_time(),
    })


@require_POST
def utme_heartbeat(request):
    """
    Lightweight polling endpoint the exam page calls periodically to
    confirm the shared timer server-side (prevents client clock
    tampering from extending exam time) and to auto-submit once the
    real clock has expired.
    """
    candidate = _require_candidate(request)
    if not candidate:
        return JsonResponse({"ok": False, "error": "not_logged_in"}, status=401)

    session = getattr(candidate, "utme_session", None)
    if not session or session.completed:
        return JsonResponse({"ok": True, "completed": True, "remaining": 0})

    if session.is_expired():
        session.grade()
        return JsonResponse({"ok": True, "completed": True, "remaining": 0})

    return JsonResponse({"ok": True, "completed": False, "remaining": session.remaining_time()})


# ============================================================
# RESULT SUMMARY (covers the whole UTME sitting)
# ============================================================
def view_result(request):
    candidate = _require_candidate(request)
    if not candidate:
        return redirect('exam_portal:exam_login')

    session = getattr(candidate, "utme_session", None)
    if not session or not session.completed:
        return redirect('exam_portal:exam_start')

    grouped_answers = {}
    for subject in session.subjects.all():
        grouped_answers[subject.subject] = []
        for q in subject.questions.all().order_by('id'):
            chosen = session.answers.get(str(q.id))
            grouped_answers[subject.subject].append({
                "question": q,
                "chosen_option": chosen,
                "correct": bool(chosen) and chosen.upper() == q.correct_answer.upper(),
            })

    return render(request, "exam_portal/result_page.html", {
        "candidate": candidate,
        "session": session,
        "result": {
            "total_score": session.score,
            "subject_scores": session.subject_scores,
        },
        "grouped_answers": grouped_answers,
    })


# ============================================================
# FIND RECORD – SEARCH BY PHONE  (rate-limited: enumeration guard)
# ============================================================
@ratelimit(key='ip', rate='15/m', block=True)
def find_record(request):
    candidate = None
    session = None

    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()
        candidate = Candidate.objects.filter(phone=phone).first()
        if candidate:
            session = getattr(candidate, "utme_session", None)
        else:
            messages.error(request, "No record found for this phone number.")

    return render(request, "exam_portal/find_record.html", {
        "candidate": candidate,
        "session": session,
    })


# ============================================================
# DOWNLOAD RECORD PDF (PUBLIC) — streams, no disk write
# ============================================================
@ratelimit(key='ip', rate='20/m', block=True)
def download_record_pdf(request, candidate_id):
    request.session["candidate_id"] = candidate_id
    return generate_result_pdf(request)


# ============================================================
# PROFESSIONAL RESULT PDF — STREAMED DIRECTLY TO THE BROWSER
# ------------------------------------------------------------
# ReportLab's canvas can write straight into the HttpResponse
# object (which behaves like a file), so nothing ever touches
# disk. This avoids unbounded storage growth from thousands of
# candidates each generating a PDF.
# ============================================================
def generate_result_pdf(request):
    cid = request.session.get("candidate_id") or request.GET.get("candidate_id")
    candidate = get_object_or_404(Candidate, id=cid)

    session = getattr(candidate, "utme_session", None)

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{candidate.full_name}_Result_Slip.pdf"'

    p = canvas.Canvas(response, pagesize=A4)  # <-- streams into `response`, no temp file
    width, height = A4

    # Background tint
    p.setFillColorRGB(0.96, 0.94, 0.90)
    p.rect(0, 0, width, height, stroke=0, fill=1)

    # Watermark
    logo_path = os.path.join(settings.BASE_DIR, "static", "img", "logo.png")
    try:
        watermark = ImageReader(logo_path)
        p.saveState()
        try:
            p.setFillAlpha(0.08)
        except Exception:
            pass
        for x in range(0, int(width), 70):
            for y in range(0, int(height), 70):
                p.drawImage(watermark, x, y, width=60, height=60, mask='auto')
        p.restoreState()
    except Exception:
        pass

    # ===== HEADER =====
    y = height - 55
    try:
        p.drawImage(logo_path, 120, y - 4, width=70, height=50, mask='auto')
    except Exception:
        pass

    center_x = width / 2

    p.setFont("Helvetica-Bold", 18)
    p.setFillColorRGB(0.07, 0.07, 0.07)
    p.drawCentredString(center_x, y + 18, "SUIBING IT SERVICES")

    p.setFont("Helvetica-Oblique", 10)
    p.drawCentredString(center_x, y + 4, "Kademawa, Garko L.G, Kano State, Nigeria")

    p.setFont("Helvetica-BoldOblique", 10)
    p.drawCentredString(center_x, y - 10, "\"Fast Secure and Reliable\"")

    p.line(100, y - 20, width - 100, y - 20)

    # ===== MAIN TITLE =====
    y -= 65
    p.setFont("Helvetica-Bold", 18)
    p.drawCentredString(center_x, y, "UTME CBT RESULT SLIP")

    # Candidate photo
    try:
        if candidate.passport:
            p.drawImage(candidate.passport.path, width - 150, y - 120, width=95, height=105, mask='auto')
    except Exception:
        pass

    # ===== Candidate Info =====
    y -= 140
    left_x = 60
    p.setFont("Helvetica", 11)
    p.drawString(left_x, y, f"Name: {candidate.full_name}")
    p.drawString(left_x + 300, y, f"Phone: {candidate.phone}")

    y -= 20
    p.drawString(left_x, y, f"CBT Login PIN: {candidate.pin}")
    p.drawString(left_x + 300, y, f"Generated: {timezone.now().strftime('%d %b %Y, %H:%M:%S')}")

    # ===== TABLE HEADER =====
    y -= 40
    table_x = 50
    table_w = width - 100
    row_h = 22

    p.setFont("Helvetica-Bold", 11)
    p.rect(table_x, y, table_w, row_h, stroke=1, fill=0)
    p.drawString(table_x + 8, y + 5, "Subject")
    p.drawString(table_x + 250, y + 5, "Questions Attempted")
    p.drawRightString(table_x + table_w - 10, y + 5, "Score")

    # ===== TABLE ROWS =====
    y -= row_h
    p.setFont("Helvetica", 10)

    total = 0.0
    if session and session.completed:
        for subject_name, score in (session.subject_scores or {}).items():
            subject_obj = session.subjects.filter(subject=subject_name).first()
            attempted = 0
            if subject_obj:
                attempted = sum(
                    1 for q in subject_obj.questions.all()
                    if str(q.id) in (session.answers or {})
                )

            p.rect(table_x, y, table_w, row_h, stroke=1, fill=0)
            p.drawString(table_x + 8, y + 6, subject_name)
            p.drawString(table_x + 260, y + 6, str(attempted))
            p.drawRightString(table_x + table_w - 10, y + 6, f"{score:.1f}")

            y -= row_h
            total += score
            if y < 120:
                p.showPage()
                y = height - 120

    # ===== TOTAL SCORE =====
    y -= 20
    p.setFont("Helvetica-Bold", 14)
    p.drawString(table_x, y, f"Total Score: {total:.1f}")

    # ===== QR CODE =====
    qr_text = (
        f"RESULT VERIFICATION\n"
        f"Name: {candidate.full_name}\n"
        f"Phone: {candidate.phone}\n"
        f"Score: {total:.1f}\n"
        f"Date: {timezone.now().strftime('%d %b %Y %H:%M:%S')}"
    )

    qr_img = qrcode.make(qr_text)
    buffer = BytesIO()
    qr_img.save(buffer, format="PNG")
    buffer.seek(0)
    qr_reader = ImageReader(buffer)

    qr_size = 120
    p.drawImage(qr_reader, (width - qr_size) / 2, y - 150, qr_size, qr_size, mask='auto')

    # ===== FOOTER =====
    p.setFont("Helvetica-Oblique", 9)
    p.drawString(table_x, 40, "Generated by Suibing IT Services Exam Portal, Kano State, Nigeria")
    p.drawRightString(table_x + table_w, 40, timezone.now().strftime("%d %b %Y %H:%M:%S"))

    p.showPage()
    p.save()
    return response


# ============================================================
# CONTACT PAGE
# ============================================================
def contact_admin(request):
    contact_info = {
        "email": "suibingitservices@gmail.com",
        "whatsapp": "+2347080195042",
        "whatsapp_link": "https://wa.me/2347080195042",
        "facebook": "https://www.facebook.com/share/17ZXnmbWqx/",
    }
    return render(request, "exam_portal/contact.html", {"contact": contact_info})


# ==============================
#  CONTACT & LIVE CHAT SYSTEM
# ==============================

@ratelimit(key='ip', rate='30/m', block=True)
@csrf_exempt
def send_contact_message(request):
    """
    Public visitor -> admin message.

    SECURITY: the thread a message belongs to is tied to a
    server-issued token stored in the visitor's own session
    (request.session['chat_thread_token']), not to whatever
    "contact" string the client happens to send. This stops one
    visitor from reading or appending to another visitor's support
    thread just by guessing/knowing their phone number — see
    ChatMessage.thread_token and fetch_messages() below.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=400)

    name = request.POST.get("name", "").strip()
    contact = request.POST.get("contact", "").strip()
    category = request.POST.get("category", "support")
    text = request.POST.get("text", "").strip() or request.POST.get("message", "").strip()

    if not text:
        return JsonResponse({"ok": False, "error": "Empty message"}, status=400)

    # One thread token per browser session. Reused across multiple
    # messages in the same session so a visitor's own conversation
    # stays together; a different browser/session gets a different
    # token and therefore cannot see this thread.
    thread_token = request.session.get("chat_thread_token")
    if not thread_token:
        thread_token = ChatMessage._meta.get_field("thread_token").default()
        request.session["chat_thread_token"] = thread_token

    candidate = None
    if contact:
        candidate = Candidate.objects.filter(phone__iexact=contact).first()

    msg = ChatMessage.objects.create(
        candidate=candidate,
        visitor_name=name[:120],
        visitor_contact=contact[:120],
        category=category,
        message=text[:5000],
        is_admin=False,
        thread_token=thread_token,
    )

    return JsonResponse({
        "ok": True,
        "id": msg.id,
        "created": msg.created_at.strftime("%d-%b-%Y %I:%M %p")
    })


@staff_member_required
@require_POST
@staff_member_required
@require_POST
def admin_send_message(request):
    """
    Admin -> visitor reply.

    The reply must carry the SAME thread_token the visitor's own
    messages used, or it will never show up for them (fetch_messages
    only returns messages matching the visitor's session token — see
    the security note there). We look that token up from the most
    recent message on this contact rather than trusting a
    client-supplied token, since the admin dashboard only ever
    supplies a phone number, not the token itself.
    """
    contact = request.POST.get("contact", "").strip()
    text = request.POST.get("text", "").strip()

    if not contact:
        return JsonResponse({"ok": False, "error": "Missing contact"}, status=400)
    if not text:
        return JsonResponse({"ok": False, "error": "Empty message"}, status=400)

    existing = ChatMessage.objects.filter(
        visitor_contact__iexact=contact
    ).order_by("-created_at").first()

    msg = ChatMessage.objects.create(
        visitor_contact=contact[:120],
        visitor_name="Admin",
        message=text[:5000],
        is_admin=True,
        thread_token=existing.thread_token if existing else "",
    )

    return JsonResponse({
        "ok": True,
        "id": msg.id,
        "created": msg.created_at.strftime("%d-%b-%Y %I:%M %p")
    })


def fetch_messages(request):
    """
    Poll for chat messages.

    - Staff (admin) view: still reads by `contact`, since admins are
      authenticated and are meant to browse any visitor's thread from
      the admin chat dashboard.
    - Public/visitor view: ONLY returns messages belonging to this
      browser's own `chat_thread_token` session value — never by the
      client-supplied `contact` param — so one visitor cannot read
      another visitor's conversation with support by guessing or
      knowing their phone number.
    """
    since_id = request.GET.get("since_id")
    is_admin_view = request.user.is_authenticated and request.user.is_staff

    qs = ChatMessage.objects.all()

    if is_admin_view:
        contact = request.GET.get("contact")
        if contact:
            qs = qs.filter(visitor_contact__iexact=contact)
    else:
        thread_token = request.session.get("chat_thread_token")
        if not thread_token:
            # This visitor has never sent a message in this session,
            # so there is nothing that could legitimately belong to
            # them yet.
            return JsonResponse({"ok": True, "messages": []})
        qs = qs.filter(thread_token=thread_token)

    if since_id:
        try:
            qs = qs.filter(id__gt=int(since_id))
        except ValueError:
            pass

    out_messages = []
    for m in qs.order_by("created_at"):
        out_messages.append({
            "id": m.id,
            "is_admin": m.is_admin,
            "message": m.message,
            "created": m.created_at.strftime("%I:%M %p"),
            "seen": m.seen,
        })

    if is_admin_view:
        ChatMessage.objects.filter(is_admin=False, seen=False).update(seen=True)
    else:
        thread_token = request.session.get("chat_thread_token")
        if thread_token:
            ChatMessage.objects.filter(thread_token=thread_token, is_admin=True, seen=False).update(seen=True)

    return JsonResponse({"ok": True, "messages": out_messages})


@staff_member_required
def admin_chat(request):
    msgs = ChatMessage.objects.all().order_by("-created_at")[:300]
    return render(request, "exam_portal/admin_chat.html", {"messages": reversed(msgs)})


# Typing indicator — now backed by django.core.cache (Redis in
# production via REDIS_URL, local-memory fallback for dev). This
# fixes the earlier bare in-process dict, which silently only worked
# for whichever single Gunicorn worker happened to handle a given
# poll request once you scale past one worker process.
TYPING_INDICATOR_TTL_SECONDS = 8  # auto-clears if no fresh "typing" ping arrives


def _typing_cache_key(contact):
    return f"admin_typing:{contact}"


@staff_member_required
@require_POST
def admin_typing(request):
    contact = (request.POST.get("contact") or "").strip()
    if not contact:
        return JsonResponse({"ok": False, "error": "Missing contact"}, status=400)
    cache.set(_typing_cache_key(contact), True, TYPING_INDICATOR_TTL_SECONDS)
    return JsonResponse({"ok": True})


def admin_typing_status(request):
    contact = (request.GET.get("contact") or "").strip()
    return JsonResponse({"typing": bool(cache.get(_typing_cache_key(contact), False))})



# ==============================
# EXPORT CHAT PDF — already streamed directly into `response`
# ==============================
@staff_member_required
def export_chat_pdf(request):
    msgs = ChatMessage.objects.all().order_by("created_at")

    response = HttpResponse(content_type="application/pdf")
    response['Content-Disposition'] = 'attachment; filename="Chat_History.pdf"'

    p = canvas.Canvas(response, pagesize=A4)
    width, height = A4

    y = height - 60

    p.setFont("Helvetica-Bold", 16)
    p.drawCentredString(width / 2, y, "LIVE CHAT MESSAGE RECORD")
    y -= 25
    p.setFont("Helvetica", 10)

    for m in msgs:
        if y < 50:
            p.showPage()
            y = height - 60

        timestamp = m.created_at.strftime("%d %b %Y %I:%M %p")
        who = "ADMIN" if m.is_admin else (m.visitor_name or "VISITOR")
        line = f"[{timestamp}] {who}: {m.message}"

        while len(line) > 95:
            p.drawString(40, y, line[:95])
            y -= 14
            line = line[95:]

        p.drawString(40, y, line)
        y -= 20

    p.showPage()
    p.save()
    return response
