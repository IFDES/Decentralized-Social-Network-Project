from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Author


class AuthorProfileForm(forms.ModelForm):
    class Meta:
        model = Author
        fields = ["display_name", "description", "profile_image", "github"]


class SignupForm(UserCreationForm):
    display_name = forms.CharField(max_length=120)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "display_name", "password1", "password2")
