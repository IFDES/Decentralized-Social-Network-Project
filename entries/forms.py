from django import forms

from .models import Entry


class EntryForm(forms.ModelForm):
    class Meta:
        model = Entry
        fields = [
            "title",
            "description",
            "content_type",
            "content",
            "visibility",
        ]


class EntryDeleteForm(forms.Form):
    confirm = forms.BooleanField(required=True, label="Yes, delete this entry")

