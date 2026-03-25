from django import template

import markdown as md


register = template.Library()


@register.filter
def render_markdown(value: str) -> str:
    """
    Render CommonMark/Markdown text to HTML for entry content.
    """
    if not value:
        return ""
    return md.markdown(value, extensions=["extra"])

