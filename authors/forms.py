from django import forms

from .models import Author


class AuthorProfileForm(forms.ModelForm):
    class Meta:
        model = Author
        fields = ["display_name", "description", "profile_image", "github"]
