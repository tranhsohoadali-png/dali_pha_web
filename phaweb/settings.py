"""
Cấu hình Django cho bản web CÔNG THỨC PHA + APP ĐIỆN THOẠI (deploy lên subdomain).
Chỉ phụ thuộc: Django + numpy. Không có xử lý ảnh (opencv...).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(key, default=None):
    return os.environ.get(key, default)


# SECURITY: đặt biến môi trường DJANGO_SECRET_KEY trên VPS.
SECRET_KEY = env('DJANGO_SECRET_KEY', 'doi-secret-key-nay-tren-vps-1234567890')

DEBUG = env('DJANGO_DEBUG', '0') == '1'

# Đặt DJANGO_ALLOWED_HOSTS="mau.tenmien.com" trên VPS (cách nhau dấu phẩy).
ALLOWED_HOSTS = [h.strip() for h in env('DJANGO_ALLOWED_HOSTS', '*').split(',') if h.strip()]

# Cho phép subdomain HTTPS gửi form (CSRF). Đặt theo domain thật.
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env('DJANGO_CSRF_TRUSTED', '').split(',') if o.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'pha',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# WhiteNoise để phục vụ static khi chạy production (nếu đã cài).
try:
    import whitenoise  # noqa: F401
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')
    _HAS_WHITENOISE = True
except Exception:
    _HAS_WHITENOISE = False

ROOT_URLCONF = 'phaweb.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'phaweb.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

LANGUAGE_CODE = 'vi'
TIME_ZONE = 'Asia/Ho_Chi_Minh'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
if _HAS_WHITENOISE:
    STORAGES = {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
    }

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Nếu chạy sau reverse-proxy (nginx) có HTTPS:
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
