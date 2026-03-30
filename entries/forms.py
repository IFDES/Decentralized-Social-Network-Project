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

    def clean(self):
        cleaned = super().clean()
        content_type = cleaned.get("content_type") or Entry.CONTENT_TEXT_PLAIN
        content = (cleaned.get("content") or "").strip()

        # Text entries require `content`.
        if content_type in dict(Entry.CONTENT_TYPE_CHOICES):
            if content_type in (Entry.CONTENT_TEXT_PLAIN, Entry.CONTENT_TEXT_MARKDOWN):
                if not content:
                    self.add_error("content", "Field 'content' is required for text entries.")
            else:
                # Image entries: `content` may be empty because uploaded images
                # are decoded/stored separately (HostedImage) and federated later.
                pass

        return cleaned


class EntryDeleteForm(forms.Form):
    confirm = forms.BooleanField(required=True, label="Yes, delete this entry")

