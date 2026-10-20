"""Local development entry point.

Usage::

    python run.py
"""

from app import create_app
from app.extensions import socketio

app = create_app()

if __name__ == "__main__":
    # Socket.IO serves both the HTTP app and the real-time transport.
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
