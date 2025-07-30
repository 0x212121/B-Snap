from fastapi.templating import Jinja2Templates
from starlette.templating import _TemplateResponse
from app.version import __version__

nav_items = [
    {
        "label": "Monitoring",
        "roles": ["admin", "operator", "viewer"],
        "items": [
            {"label": "📍 Maps", "href": "/maps", "roles": ["admin", "operator", "viewer"]},
            {"label": "🖼️ Snapshots", "href": "/snap_gallery", "roles": ["admin", "operator"]},
            {"label": "🎞️ Videos", "href": "/videos", "roles": ["admin", "operator"]},
        ],
    },
    {
        "label": "Devices",
        "roles": ["admin"],
        "items": [
            {"label": "📷 Cameras", "href": "/cameras", "roles": ["admin"]},
            {"label": "📡 NVRs", "href": "/nvrs", "roles": ["admin"]},
            {"label": "🟢 Status", "href": "/health", "roles": ["admin"]},
        ],
    },
    {
        "label": "Administration",
        "roles": ["admin"],
        "items": [
            {"label": "👤 Users", "href": "/users", "roles": ["admin"]},
            {"label": "✅ WA Whitelist", "href": "/admin/whitelist", "roles": ["admin"]},
            {"label": "⚙️ Config", "href": "/config", "roles": ["admin"]},
        ],
    },
    {
        "label": "Logs",
        "roles": ["admin"],
        "items": [
            {"label": "📄 Logs Viewer", "href": "/logs", "roles": ["admin"]},
            {"label": "🕵️ Audit Logs", "href": "/audit-logs", "roles": ["admin"]},
        ],
    },
    {
        "label": "Developer",
        "roles": ["admin"],
        "items": [
            {"label": "📘 API Docs", "href": "/developer/docs", "roles": ["admin"]},
            {"label": "📝 Changelog", "href": "/changelog", "roles": ["admin"]},
        ],
    },
    {
        "label": "Analytics",
        "roles": ["admin"],
        "items": [
            {"label": "📊 Stats", "href": "/stats", "roles": ["admin"]},
        ],
    },
]

    
class TemplatesWithExtras(Jinja2Templates):
    def TemplateResponse(self, name: str, context: dict, *args, **kwargs) -> _TemplateResponse:
        user_role = context.get("request").session.get("user_role", "viewer")

        # Filter nav_items sesuai role
        filtered_nav = []
        for section in nav_items:
            if user_role in section["roles"]:
                filtered_section = {
                    "label": section["label"],
                    "roles": section["roles"],
                    "items": [item for item in section["items"] if user_role in item["roles"]]
                }
                # Skip jika semua item-nya tidak cocok
                if filtered_section["items"]:
                    filtered_nav.append(filtered_section)

        context["nav_items"] = filtered_nav
        context.setdefault("version", __version__)
        return super().TemplateResponse(name, context, *args, **kwargs)


templates = TemplatesWithExtras(directory="templates")
templates.env.globals["version"] = __version__  # Optional if used in macros/static content
