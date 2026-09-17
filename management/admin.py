from django.contrib import admin  # noqa: F401

# NOTE: this used to register duplicate ExamYear / SubjectYear /
# Question models that were never actually used. The real models
# and their admin registration live in exams/admin.py.
