from django import template

register = template.Library()


@register.filter(name="add_class")
def add_class(field, css_class):
    """Add a CSS class to a form field widget. Usage: {{ form.field|add_class:'form-control' }}"""
    return field.as_widget(attrs={"class": css_class})


@register.filter(name="add_error_class")
def add_error_class(field, css_class):
    """
    Adds a CSS class to a form field widget
    if the field contains validation errors.

    Usage:
        {{ form.name|add_error_class:"is-invalid" }}
    """

    if not hasattr(field, "as_widget"):
        return field

    existing_classes = field.field.widget.attrs.get("class", "")

    if field.errors:
        classes = f"{existing_classes} {css_class}".strip()
    else:
        classes = existing_classes

    return field.as_widget(attrs={"class": classes})
