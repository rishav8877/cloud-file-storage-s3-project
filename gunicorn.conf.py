"""Shared startup settings for local runs and AWS Elastic Beanstalk."""
import os

from dotenv import load_dotenv

load_dotenv()

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get('WEB_CONCURRENCY', '2'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
accesslog = '-'
errorlog = '-'
# Workers share the development fallback session key as well as application code.
preload_app = True
