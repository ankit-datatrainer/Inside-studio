"""WSGI entry point for production servers (gunicorn / waitress / uWSGI).

    gunicorn -w 2 -b 0.0.0.0:8000 wsgi:app
    waitress-serve --port=8000 wsgi:app
"""
from app import app

if __name__ == "__main__":
    app.run()
