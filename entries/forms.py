from django import forms

from .models import Entry


VISIBILITY_CHOICES_FOR_ENTRY = [
    (Entry.VISIBILITY_PUBLIC, "Public"),
    (Entry.VISIBILITY_FRIENDS, "Friends only"),
    (Entry.VISIBILITY_UNLISTED, "Unlisted"),
]


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visibility"].choices = VISIBILITY_CHOICES_FOR_ENTRY


class EntryDeleteForm(forms.Form):
    confirm = forms.BooleanField(required=True, label="Yes, delete this entry")

