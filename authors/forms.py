from django import forms
from django.contrib.auth.models import User

from .models import Author


class AuthorProfileForm(forms.ModelForm):
    class Meta:
        model = Author
        fields = ["display_name", "description", "profile_image", "github"]


class SignupForm(forms.Form):
    username = forms.CharField(max_length=150)
    display_name = forms.CharField(max_length=120)
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("A user with that username already exists.")
        return username

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned
