"""
WSGI config for aivision_backend project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

from .otlp_wsgi import ChunkedOtlpBody

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aivision_backend.settings')

application = ChunkedOtlpBody(get_wsgi_application())
