from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.security import is_authenticated

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["is_authenticated"] = is_authenticated
