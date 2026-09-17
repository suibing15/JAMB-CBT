from django.contrib import admin
from .models import ExamYear, ExamSubject, Question

@admin.register(ExamYear)
class ExamYearAdmin(admin.ModelAdmin):
    list_display = ('year','active')

@admin.register(ExamSubject)
class ExamSubjectAdmin(admin.ModelAdmin):
    list_display = ('exam_year','subject','questions_to_display')
    list_filter = ('exam_year',)

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('exam_subject','question_text','mark')
    list_filter = ('exam_subject__exam_year','exam_subject__subject')
    search_fields = ('question_text',)
