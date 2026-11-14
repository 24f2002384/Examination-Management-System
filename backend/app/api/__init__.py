from flask import Blueprint

api = Blueprint("api", __name__, url_prefix="/api/v1")

from . import routes  # noqa: E402,F401
from . import admin_routes  # noqa: E402,F401
from . import portal_routes  # noqa: E402,F401
from . import jobs_routes  # noqa: E402,F401
from . import google_mail_routes  # noqa: E402,F401
