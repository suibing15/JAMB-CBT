from django.http import HttpResponse


def ratelimited_view(request, exception=None):
    """
    Shown instead of a raw exception/traceback whenever a request is
    blocked by django-ratelimit (too many login attempts, too many
    PDF downloads, chat spam, etc). django-ratelimit's `Ratelimited`
    exception is a subclass of Django's PermissionDenied, so Django
    automatically routes it here once this is registered as
    `handler403` in the root URLconf. Kept deliberately simple and
    dependency-free so it can never itself fail to render.
    """
    return HttpResponse(
        """
        <div style="max-width:480px;margin:80px auto;text-align:center;
                    font-family:sans-serif;padding:24px;">
            <h2 style="color:#d9534f;">⏳ Too Many Requests</h2>
            <p>You've made too many requests in a short time.
               Please wait a moment and try again.</p>
            <p style="color:#888;font-size:13px;">
               If this keeps happening, contact support.
            </p>
        </div>
        """,
        status=429,
        content_type="text/html",
    )
