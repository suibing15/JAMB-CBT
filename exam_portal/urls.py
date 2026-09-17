from django.urls import path
from . import views

app_name = "exam_portal"

urlpatterns = [
    path('', views.exam_login, name="exam_login"),

    # ---- Native JAMB-style UTME flow (all subjects, one timer) ----
    path('start/', views.exam_start, name="exam_start"),
    path('start/begin/', views.begin_utme, name="begin_utme"),
    path('utme/', views.utme_exam, name="utme_exam"),
    path('utme/heartbeat/', views.utme_heartbeat, name="utme_heartbeat"),

    # CONTACT + LIVE CHAT
    path('contact/', views.contact_admin, name='contact_admin'),
    path('contact/send/', views.send_contact_message, name='send_contact_message'),
    path('contact/fetch/', views.fetch_messages, name='fetch_messages'),
    path('contact/admin-send/', views.admin_send_message, name='admin_send_message'),
    # ADMIN CHAT PAGE
    path('admin-chat/', views.admin_chat, name='admin_chat'),
    path("chat/admin-typing/", views.admin_typing, name="admin_typing"),
    path("chat/admin-typing-status/", views.admin_typing_status, name="admin_typing_status"),

    # Export Chat PDF
    path("chat-export/", views.export_chat_pdf, name="chat_export"),

    # FIND RECORD
    path('find-record/', views.find_record, name='find_record'),
    path('download-record/<int:candidate_id>/', views.download_record_pdf, name='download_record_pdf'),

    # RESULTS
    path('result/', views.view_result, name='view_result'),
    path('result/pdf/', views.generate_result_pdf, name='generate_result_pdf'),
]
