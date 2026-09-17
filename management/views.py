from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.conf import settings
from accounts.models import Candidate
from exams.models import ExamYear, ExamSubject, Question
from .forms import ExamYearForm, UploadXLSXForm
import openpyxl
import zipfile
from io import BytesIO


# -------- ROLE PERMISSION CONTROL --------
def in_groups(user, groups):
    return user.is_active and (user.is_superuser or user.groups.filter(name__in=groups).exists())


def role_required(groups, message=None):
    """
    Like the plain user_passes_test(..., login_url=...) used before,
    but if the person is ALREADY logged in and simply lacks the
    required role, we now show a clear "you don't have permission"
    message before bouncing them to the login page — instead of
    silently redirecting with no explanation, which is exactly what
    made "Download All PDFs" look like it did nothing for anyone who
    wasn't in the Admin group.
    """
    def decorator(view_func):
        def wrapped(request, *args, **kwargs):
            if in_groups(request.user, groups):
                return view_func(request, *args, **kwargs)
            if request.user.is_authenticated:
                messages.error(
                    request,
                    message or "You don't have permission to do that. "
                                "This requires the Admin role."
                )
            return redirect(f'/management/login/?next={request.path}')
        return wrapped
    return decorator


# -------- LOGIN --------
def staff_login(request):
    if request.user.is_authenticated:
        return redirect('management:dashboard')
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user and user.is_active:
            login(request, user)
            return redirect('management:dashboard')
        messages.error(request, "Invalid credentials")

    return render(request, "management/login.html")


def staff_logout(request):
    logout(request)
    return redirect('management:login')


# -------- DASHBOARD --------
@login_required(login_url='/management/login/')
@role_required(['Admin','DataEntry','Viewer'])
def dashboard(request):
    total_candidates = Candidate.objects.count()
    candidates = Candidate.objects.all().order_by('-date_registered')
    exam_years = ExamYear.objects.all().order_by('-year')

    return render(request, "management/dashboard.html", {
        "total_candidates": total_candidates,
        "candidates": candidates,
        "exam_years": exam_years,
        # Controls whether the "Bulk Download" and "Full Admin Panel"
        # links even render — matches the @role_required(['Admin'])
        # on download_all_pdfs, so a DataEntry/Viewer user never sees
        # a link that would just bounce them to login.
        "is_admin": in_groups(request.user, ['Admin']),
        "admin_url": settings.ADMIN_URL_PATH,
    })


# -------- EXAM YEAR LIST --------
@login_required(login_url='/management/login/')
@role_required(['Admin','DataEntry','Viewer'])
def exam_year_list(request):
    years = ExamYear.objects.all().order_by('-year')
    return render(request, "management/exam_year_list.html", {"years": years})


# -------- ADD EXAM YEAR --------
@login_required(login_url='/management/login/')
@role_required(['Admin'])
def exam_year_add(request):
    if request.method == "POST":
        form = ExamYearForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ Exam Year Added Successfully")
            return redirect('management:exam_year_list')
    else:
        form = ExamYearForm()
    return render(request, "management/exam_year_add.html", {"form": form})


# -------- SUBJECT LIST FOR A YEAR --------
@login_required(login_url='/management/login/')
@role_required(['Admin','DataEntry','Viewer'])
def subject_list(request, year_id):
    year = get_object_or_404(ExamYear, id=year_id)
    subjects = ExamSubject.objects.filter(exam_year=year)
    return render(request, "management/subject_list.html", {"year": year, "subjects": subjects})


# -------- QUESTION LIST FOR A SUBJECT --------
@login_required(login_url='/management/login/')
@role_required(['Admin','DataEntry','Viewer'])
def question_list(request, subject_id):
    subject = get_object_or_404(ExamSubject, id=subject_id)
    questions = Question.objects.filter(exam_subject=subject).order_by('id')
    return render(request, "management/question_list.html", {"subject": subject, "questions": questions})


# -------- BULK QUESTION UPLOAD --------
@login_required(login_url='/management/login/')
@role_required(['Admin','DataEntry'])
def upload_questions(request, subject_id):
    subject = get_object_or_404(ExamSubject, id=subject_id)

    if request.method == "POST":
        form = UploadXLSXForm(request.POST, request.FILES)
        if form.is_valid():
            wb = openpyxl.load_workbook(request.FILES['file'])
            ws = wb.active
            created = 0
            first_row = True

            for row in ws.iter_rows(values_only=True):
                if first_row:
                    first_row = False
                    continue

                question_text, a, b, c, d, correct, mark = row[:7]
                if not question_text:
                    continue

                Question.objects.create(
                    exam_subject = subject,
                    question_text = question_text,
                    option_a = a or "",
                    option_b = b or "",
                    option_c = c or "",
                    option_d = d or "",
                    correct_answer = (correct or "A").upper(),
                    mark = float(mark) if mark else 1.0   # ✅ FIXED
                )
                created += 1

            messages.success(request, f"✅ {created} questions uploaded successfully.")
            return redirect('management:question_list', subject_id=subject_id)

    else:
        form = UploadXLSXForm()

    return render(request, "management/upload_questions.html", {"form": form, "subject": subject})

# -------- BULK PDF DOWNLOAD --------
# Rebuilt to generate every PDF on the fly, in memory, and stream a
# single ZIP straight to the browser. Nothing is written to disk at
# any point — this matters a lot once storage is Supabase/S3-backed
# rather than a local filesystem.
@login_required(login_url='/management/login/')
@role_required(['Admin'])
def download_all_pdfs(request):
    from accounts.views import _build_registration_pdf_bytes  # local import avoids circular import

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for c in Candidate.objects.all():
            try:
                pdf_bytes = _build_registration_pdf_bytes(c)
            except Exception:
                continue
            safe_name = "".join(ch for ch in c.full_name if ch.isalnum() or ch in " _-").strip() or f"candidate_{c.id}"
            zipf.writestr(f"{safe_name}_{c.phone}.pdf", pdf_bytes)

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer.read(), content_type="application/zip")
    response['Content-Disposition'] = 'attachment; filename="All_Registration_Forms.zip"'
    return response
