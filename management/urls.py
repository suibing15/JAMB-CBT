from django.urls import path
from . import views

app_name = "management"

urlpatterns = [
    path('', views.dashboard, name="dashboard"),
    path('login/', views.staff_login, name="login"),
    path('logout/', views.staff_logout, name="logout"),

    # Exam Management
    path('exam-years/', views.exam_year_list, name="exam_year_list"),
    path('exam-years/add/', views.exam_year_add, name="exam_year_add"),
    path('subjects/<int:year_id>/', views.subject_list, name="subject_list"),
    path('subjects/<int:subject_id>/questions/', views.question_list, name="question_list"),
    path('subjects/<int:subject_id>/questions/upload/', views.upload_questions, name="upload_questions"),

    # ✅ Bulk Download PDFs
    path('download-all-pdfs/', views.download_all_pdfs, name="download_all_pdfs"),
]
