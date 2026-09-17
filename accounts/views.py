from django.shortcuts import render, redirect, get_object_or_404
from .models import Candidate, SUBJECT_CHOICES
from django.core.files.base import ContentFile
from django.http import HttpResponse
from django.conf import settings
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
import base64
import os
from io import BytesIO

try:
    from django_ratelimit.decorators import ratelimit
except ImportError:  # pragma: no cover
    def ratelimit(*a, **kw):
        def _wrap(fn):
            return fn
        return _wrap


def home(request):
    return render(request, "home.html")

@ratelimit(key='ip', rate='6/h', block=True)
def register_candidate(request):
    if request.method == "POST":
        signature_data = request.POST.get("signature_data")

        candidate = Candidate.objects.create(
            passport=request.FILES.get("passport"),
            full_name=request.POST.get("full_name"),
            phone=request.POST.get("phone"),
            subject1=request.POST.get("subject1"),
            subject2=request.POST.get("subject2"),
            subject3=request.POST.get("subject3"),
            subject4=request.POST.get("subject4"),
            address=request.POST.get("address"),
            cbt_preference=request.POST.get("cbt_preference"),
        )

        # ✅ Save signature image
        if signature_data and signature_data.startswith("data:image/png;base64,"):
            format, imgstr = signature_data.split(";base64,")
            signature_file = ContentFile(base64.b64decode(imgstr), name=f"signature_{candidate.id}.png")
            candidate.signature = signature_file
            candidate.save()

        # ✅ PIN is now auto-generated in model. No need to set manually.

        return redirect("registration_success", candidate_id=candidate.id)

    return render(request, "register.html", {"subjects": SUBJECT_CHOICES})


def registration_success(request, candidate_id):
    candidate = get_object_or_404(Candidate, id=candidate_id)
    return render(request, "registration_success.html", {"candidate": candidate})


def _build_registration_pdf_bytes(candidate):
    """
    Builds the registration-form PDF entirely in memory and returns
    the raw bytes. Shared by the single-candidate download view and
    the bulk "download all" ZIP builder — nothing here ever touches
    disk.
    """
    # NOTE: this used to be the bare relative path "static/img/logo.png",
    # which only resolved correctly if the process's current working
    # directory happened to be the project root — not guaranteed
    # under Gunicorn/systemd/most deploy setups, where CWD can be
    # anywhere. Resolved through BASE_DIR instead so it works
    # regardless of the process's working directory.
    logo_path = os.path.join(settings.BASE_DIR, "static", "img", "logo.png")

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # ✅ Background watermark
    try:
        watermark = ImageReader(logo_path)
        p.saveState()
        p.setFillAlpha(0.12)
        tile_size = 110
        for x in range(0, int(width), tile_size):
            for y in range(0, int(height), tile_size):
                p.drawImage(watermark, x, y, width=90, height=90, mask='auto')
        p.restoreState()
    except Exception:
        pass

    # Header
    y = height - 70
    try:
        p.drawImage(logo_path, 60, y - 20, width=65, height=65, mask='auto')
    except Exception:
        pass

    p.setFont("Helvetica-Bold", 20)
    p.drawString(150, y + 5, "SUIBING IT SERVICES")
    p.setFont("Helvetica-Oblique", 12)
    p.drawString(150, y - 15, '"Fast Secure and  Reliable"')
    p.drawString(150, y - 35, "Kademawa, Garko L.G, Kano State, Nigeria")
    y -= 55
    p.line(50, y, width - 50, y)
    y -= 35

    p.setFont("Helvetica-Bold", 16)
    p.drawCentredString(width / 2, y, "JAMB TRAINING REGISTRATION FORM")

    # Passport
    try:
        p.drawImage(candidate.passport.path, width - 170, y - 200, width=120, height=150, mask='auto')
    except Exception:
        pass

    y -= 120
    p.setFont("Helvetica", 12)

    # ✅ PIN now included correctly as exam_pin
    info = [
        ("Full Name:", candidate.full_name),
        ("Phone (Login ID):", candidate.phone),
       ("CBT LOGIN PIN (Important):", candidate.pin),   # ✅ Correct field name
        ("Subjects:", f"{candidate.subject1}, {candidate.subject2}, {candidate.subject3}, {candidate.subject4}"),
        ("CBT Preference:", candidate.cbt_preference),
        ("Address:", candidate.address),
        ("Date Registered:", candidate.date_registered.strftime("%d - %b - %Y")),
    ]

    for label, value in info:
        p.drawString(60, y, f"{label} {value}")
        y -= 22

    # Signature
    y -= 50
    p.setFont("Helvetica-Bold", 12)
    p.drawString(60, y, "Candidate Signature:")
    y -= 75

    try:
        p.drawImage(candidate.signature.path, 60, y, width=170, height=70, mask='auto')
    except Exception:
        pass

    p.save()
    buffer.seek(0)
    return buffer.read()


@ratelimit(key='ip', rate='20/m', block=True)
def download_pdf(request, candidate_id):
    """
    Streams the registration form PDF straight to the browser.
    Nothing is written to disk — this keeps storage usage flat no
    matter how many thousands of candidates register (important on
    Supabase/Render style hosting where local disk isn't persistent
    anyway, and where storage is often billed).
    """
    candidate = get_object_or_404(Candidate, id=candidate_id)
    pdf_bytes = _build_registration_pdf_bytes(candidate)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response['Content-Disposition'] = f'attachment; filename="{candidate.full_name}_Registration_Form.pdf"'
    return response
