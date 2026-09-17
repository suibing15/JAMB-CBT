from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """
    Usage in template:
      {% load dict_extras %}
      {{ mydict|get_item:variable_key }}
    Returns dictionary.get(str(key)) — keys are stored as strings in session JSON.
    """
    try:
        # convert key to string because answers keys are saved as strings
        return dictionary.get(str(key))
    except Exception:
        return None
