from django.db import models  # noqa: F401

# NOTE: this app used to define its own duplicate ExamYear /
# SubjectYear / Question models that were never actually used by
# any view — the real, live models are in exams/models.py, which
# every view in this app (see management/views.py) already imports
# from. The dead duplicates were removed here to stop future
# confusion about which model is authoritative. No models currently
# live in this app; it still exists as a Django app because
# management/urls.py and management/views.py belong to it.
