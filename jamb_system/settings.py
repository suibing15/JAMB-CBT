from pathlib import Path
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    val = os.environ.get(name, default)
    return [item.strip() for item in val.split(",") if item.strip()]


# ====================================================
# 🔐 SECURITY
# ====================================================
# There is NO insecure fallback in production. If DJANGO_SECRET_KEY
# is missing while DEBUG=False, Django will refuse to start rather
# than silently run with a guessable key.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")

DEBUG = env_bool("DJANGO_DEBUG", default=False)

if not SECRET_KEY:
    if DEBUG:
        # Only ever used for local development.
        SECRET_KEY = "django-insecure-dev-key-ONLY-FOR-LOCAL-DEVELOPMENT"
    else:
        raise RuntimeError(
            "DJANGO_SECRET_KEY environment variable is not set. "
            "Refusing to start in production without it."
        )

# No wildcard "*" host in production — set explicitly via env var.
ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    default="jamb.suibingitservces.online,.onrender.com,.ngrok-free.app" if DEBUG else "",
)
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["*"]

CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=(
        "https://*.onrender.com,https://*.ngrok-free.app,"
        "https://*.ngrok.io,https://jamb.suibingitservces.online"
    ),
)

# ✅ Proxy / TLS termination (Render, Railway, etc.)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ✅ Cookie / CSRF security
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 4  # 4 hours — plenty for one UTME sitting
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# ✅ HSTS + misc hardening (only meaningful once served over HTTPS)
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_HSTS_SECONDS = 0 if DEBUG else 60 * 60 * 24 * 30  # 30 days
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

# ✅ Upload limits — prevents disk/memory exhaustion via oversized
# passport photos / signatures / bulk XLSX uploads.
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024   # 8 MB per request
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024   # 5 MB per file kept in memory before spooling


# ====================================================
# 🔧 APPLICATIONS
# ====================================================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "accounts",
    "management",
    "exams",
    "exam_portal",
]


# ====================================================
# 🔧 MIDDLEWARE
# ------------------------------------------------------------
# NOTE: the old Mega-server "remote license kill switch"
# middleware (LicenseGuardMiddleware / LicenseLockMiddleware) has
# been permanently removed from this codebase. This app can no
# longer be remotely disabled by a third-party server.
# ====================================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    # ✅ STATIC FILES (RENDER)
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ====================================================
# 🔧 URLS & WSGI
# ====================================================
ROOT_URLCONF = "jamb_system.urls"
WSGI_APPLICATION = "jamb_system.wsgi.application"


# ====================================================
# 🔧 TEMPLATES
# ====================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ====================================================
# 🗄 DATABASE — Supabase (Postgres) ready
# ------------------------------------------------------------
# Set DATABASE_URL to your Supabase connection string, e.g.:
#
#   postgresql://postgres.<project-ref>:<password>@
#   aws-0-<region>.pooler.supabase.com:6543/postgres
#
# Use the PORT 6543 "Transaction" pooler endpoint (PgBouncer) for
# normal web traffic — it's what Supabase recommends for
# serverless/many-short-lived-connections workloads like Django
# behind Gunicorn. Falls back to local SQLite if DATABASE_URL is
# not set, so local development still works with zero config.
# ====================================================
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=env_bool("DATABASE_SSL_REQUIRE", default=True),
        )
    }
    # Supabase's pooled (PgBouncer transaction-mode) endpoint does not
    # support Django's server-side cursors / persistent prepared
    # statements — disable them to avoid intermittent errors.
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# ====================================================
# 🧠 CACHE — backs rate limiting (django-ratelimit) and the
# login brute-force lockout counters.
# ------------------------------------------------------------
# Set REDIS_URL in production (Render/Upstash/Supabase-adjacent
# Redis all work) for correctness across multiple gunicorn workers.
# Falls back to Django's local-memory cache for local dev — NOTE:
# LocMemCache is per-process, so rate limiting will NOT be
# consistent across multiple gunicorn workers in production if you
# skip setting REDIS_URL.
# ====================================================
REDIS_URL = os.environ.get("REDIS_URL")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "jamb-cbt-cache",
        }
    }

RATELIMIT_USE_CACHE = "default"
# Fail CLOSED (block the request) if the cache backend itself is
# unreachable, rather than silently disabling rate limiting.
RATELIMIT_FAIL_OPEN = False


# ====================================================
# 🌍 INTERNATIONALIZATION
# ====================================================
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True


# ====================================================
# 📁 STATIC FILES
# ====================================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# ====================================================
# 📁 MEDIA FILES — Supabase Storage (S3-compatible) ready
# ------------------------------------------------------------
# Supabase Storage exposes an S3-compatible API, so we route
# uploads (passport photos, signatures, question images) through
# django-storages' S3 backend when the relevant env vars are
# present. This also matters for Render/most PaaS hosts, whose
# local disks are EPHEMERAL — anything saved locally disappears on
# every redeploy. Falls back to local disk storage for local dev.
#
# Required env vars once you create a Supabase Storage bucket:
#   AWS_ACCESS_KEY_ID       -> Supabase S3 access key
#   AWS_SECRET_ACCESS_KEY   -> Supabase S3 secret key
#   AWS_STORAGE_BUCKET_NAME -> your bucket name, e.g. "jamb-media"
#   AWS_S3_ENDPOINT_URL     -> https://<project-ref>.supabase.co/storage/v1/s3
#   AWS_S3_REGION_NAME      -> e.g. "us-east-1" (Supabase accepts any)
# ====================================================
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

USE_SUPABASE_STORAGE = env_bool("USE_SUPABASE_STORAGE", default=bool(os.environ.get("AWS_STORAGE_BUCKET_NAME")))

if USE_SUPABASE_STORAGE:
    INSTALLED_APPS = INSTALLED_APPS + ["storages"]

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL")
    AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "us-east-1")
    AWS_DEFAULT_ACL = None          # Supabase buckets manage ACL via policy, not per-object ACL
    AWS_QUERYSTRING_AUTH = True     # signed URLs (works with private buckets)
    AWS_S3_FILE_OVERWRITE = False


# ====================================================
# DEFAULT
# ====================================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ====================================================
# 🔐 OBSCURED ADMIN URL — single source of truth
# ------------------------------------------------------------
# Both jamb_system/urls.py (which mounts admin.site.urls here) and
# any template/view that needs to link to the admin panel (see
# management/views.py::dashboard, management/templates/.../dashboard.html)
# read this ONE setting, instead of each computing their own copy of
# the env var and risking them drifting out of sync.
# ====================================================
ADMIN_URL_PATH = os.environ.get("DJANGO_ADMIN_URL", "secure-admin-4932/")
if not ADMIN_URL_PATH.endswith("/"):
    ADMIN_URL_PATH += "/"


# ====================================================
# 🧪 JAMB CBT DOMAIN SETTINGS
# ====================================================
# Standard real-world UTME sitting length in minutes. Override via
# env var if the training center wants a different mock duration.
JAMB_TOTAL_DURATION_MINUTES = int(os.environ.get("JAMB_TOTAL_DURATION_MINUTES", "130"))


# ====================================================
# 📝 LOGGING — surfaces rate-limit blocks & auth failures instead
# of silently swallowing them.
# ====================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
