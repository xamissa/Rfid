"""Self-contained offline test settings; never reads deployment configuration."""
SECRET_KEY = 'offline-tests-only'
ODOO_CREDENTIAL_ENCRYPTION_KEY = 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'bridge_core']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'Africa/Johannesburg'
ROOT_URLCONF = 'config.urls'
MIDDLEWARE = ['django.contrib.sessions.middleware.SessionMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'bridge_core.middleware.RequireInitialAdminPasswordChangeMiddleware']
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
ALLOW_PHYSICAL_READER_CONTACT = False
ALLOW_ODOO_CONTACT = False
READER_BACKEND = 'disabled'
SENDER_BACKEND = 'disabled'
STATIC_URL = '/static/'
ALLOWED_HOSTS = ['testserver']
LOGIN_URL = 'login'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
CONFIGURATION = {}
