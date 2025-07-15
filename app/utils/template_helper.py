from fastapi.templating import Jinja2Templates
from app.version import __version__

templates = Jinja2Templates(directory="templates")
templates.env.globals["version"] = __version__
