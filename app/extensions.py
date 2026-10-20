"""Centralized Flask extension instances.

Extensions are created here and bound to the application inside the app
factory in ``app/__init__.py``. Keeping them separate from the factory avoids
circular imports and makes the extensions importable from models, routes, and
tests.
"""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
# Real-time collaboration transport (issue #50). The server uses the threaded
# async mode so it works with the stock development server and tests; production
# deployments can select another mode through ``SOCKETIO_ASYNC_MODE``.
socketio = SocketIO()

# Where unauthenticated users are redirected.
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"
