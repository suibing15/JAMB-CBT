from django import forms
from exams.models import ExamYear

class ExamYearForm(forms.ModelForm):
    class Meta:
        model = ExamYear
        fields = ['year']


class UploadXLSXForm(forms.Form):
    file = forms.FileField()
