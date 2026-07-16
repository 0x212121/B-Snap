from fastapi.templating import Jinja2Templates
from starlette.templating import _TemplateResponse
from app.version import __version__
import logging

logger = logging.getLogger(__name__)

# SVG Icons mapping (nama -> HTML SVG)
ICONS = {
    "map": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"/><line x1="9" y1="3" x2="9" y2="18"/><line x1="15" y1="6" x2="15" y2="21"/></svg>',
    "camera": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>',
    "image": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>',
    "video": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 8-6 4 6 4V8Z"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>',
    "hard-drive": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></svg>',
    "activity": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
    "users": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    "shield-check": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/><path d="m9 12 2 2 4-4"/></svg>',
    "mail": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>',
    "settings": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>',
    "clock": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    "trash": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>',
    "file-text": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>',
    "search": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>',
    "book-open": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>',
    "git-branch": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>',
    "bar-chart": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>',
    "pie-chart": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/></svg>',
    "grid": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
    "mail-open": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.2 8.4c.5.38.8.97.8 1.6v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V10a2 2 0 0 1 .8-1.6l8-6a2 2 0 0 1 2.4 0l8 6Z"/><path d="m22 10-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 10"/></svg>',
}

# Struktur nav_items dengan nama icon
nav_items = [
    {
        "label": "Monitoring",
        "icon": "activity",
        "roles": ["admin", "operator", "viewer"],
        "items": [
            {"label": "Maps", "href": "/maps", "icon": "map", "roles": ["admin", "operator", "viewer"]},
            {"label": "Snapshots", "href": "/snap_gallery", "icon": "image", "roles": ["admin", "operator"]},
            {"label": "Videos", "href": "/videos", "icon": "video", "roles": ["admin", "operator"]},
        ],
    },
    {
        "label": "Devices",
        "icon": "hard-drive",
        "roles": ["admin"],
        "items": [
            {"label": "Cameras", "href": "/cameras", "icon": "camera", "roles": ["admin"]},
            {"label": "NVRs", "href": "/nvrs", "icon": "hard-drive", "roles": ["admin"]},
            {"label": "Record Checks", "href": "/admin/record-checks", "icon": "activity", "roles": ["admin"]},
            {"label": "Status", "href": "/health", "icon": "activity", "roles": ["admin"]},
        ],
    },
    {
        "label": "Administration",
        "icon": "settings",
        "roles": ["admin"],
        "items": [
            {"label": "Users", "href": "/users", "icon": "users", "roles": ["admin"]},
            {"label": "Camera Groups", "href": "/admin/camera-groups", "icon": "grid", "roles": ["admin"]},
            {"label": "WA Whitelist", "href": "/admin/whitelist", "icon": "shield-check", "roles": ["admin"]},
            {"label": "Email Recipients", "href": "/recipients", "icon": "mail", "roles": ["admin"]},
            {"label": "Email Templates", "href": "/email-templates", "icon": "mail-open", "roles": ["admin"]},
            {"label": "Configuration", "href": "/config", "icon": "settings", "roles": ["admin"]},
            {"label": "Job Management", "href": "/admin/jobs", "icon": "clock", "roles": ["admin"]},
            {"label": "Trash Management", "href": "/admin/trash", "icon": "trash", "roles": ["admin"]},
        ],
    },
    {
        "label": "Logs",
        "icon": "file-text",
        "roles": ["admin"],
        "items": [
            {"label": "Logs Viewer", "href": "/logs", "icon": "file-text", "roles": ["admin"]},
            {"label": "Audit Logs", "href": "/audit-logs", "icon": "search", "roles": ["admin"]},
            {"label": "Email Logs", "href": "/email-logs", "icon": "mail", "roles": ["admin"]},
        ],
    },
    {
        "label": "Developer",
        "icon": "book-open",
        "roles": ["admin"],
        "items": [
            {"label": "API Docs", "href": "/developer/docs", "icon": "book-open", "roles": ["admin"]},
            {"label": "Environment", "href": "/docs/environment", "icon": "settings", "roles": ["admin"]},
            {"label": "Documentation", "href": "/documentation/index.html", "icon": "git-branch", "roles": ["admin"], "external": True},
        ],
    },
    {
        "label": "Analytics",
        "icon": "bar-chart",
        "roles": ["admin"],
        "items": [
            {"label": "Dashboard", "href": "/analytics", "icon": "bar-chart", "roles": ["admin"]},
        ],
    },
]

class TemplatesWithExtras(Jinja2Templates):
    def TemplateResponse(self, name: str, context: dict, *args, **kwargs) -> _TemplateResponse:
        try:
            request = context.get("request")
            
            # Default values kalau request tidak ada
            user_role = "viewer"
            user_name = "User"
            current_path = "/"
            timezone = "UTC"
            debug_mode = False
            
            if request is not None:
                # Extract dengan aman
                if hasattr(request, 'session') and request.session:
                    user_role = request.session.get("user_role", "viewer")
                    user_name = request.session.get("user_name", "User")
                
                if hasattr(request, 'url') and request.url:
                    current_path = getattr(request.url, 'path', '/')
                
                if hasattr(request, 'state') and request.state:
                    timezone = getattr(request.state, 'timezone', 'UTC')
                    debug_mode = getattr(request.state, 'debug_mode', False)
            
            # Filter navigation dengan SVG icon conversion
            filtered_nav = []
            for section in nav_items:
                # Cek role section
                section_roles = section.get("roles", [])
                if user_role not in section_roles:
                    continue
                
                # Filter items dalam section
                filtered_items = []
                for item in section.get("items", []):
                    item_roles = item.get("roles", [])
                    if user_role not in item_roles:
                        continue
                    
                    # Cek active state
                    item_path = item.get("href", "")
                    is_active = False
                    if current_path and item_path and item_path != "/":
                        is_active = current_path.startswith(item_path)
                    elif current_path == "/" and item_path == "/":
                        is_active = True
                    
                    # Convert nama icon ke SVG HTML
                    icon_name = item.get("icon", "")
                    icon_svg = ICONS.get(icon_name, "")
                    
                    filtered_items.append({
                        "label": item.get("label", ""),
                        "href": item_path,
                        "icon": icon_svg,  # Sekarang ini SVG HTML string
                        "is_active": is_active,
                        "external": item.get("external", False),
                    })
                
                # Hanya tambahkan section kalau ada items yang visible
                if filtered_items:
                    section_active = any(item["is_active"] for item in filtered_items)
                    
                    # Convert section icon juga
                    section_icon_name = section.get("icon", "")
                    section_icon_svg = ICONS.get(section_icon_name, "")
                    
                    filtered_nav.append({
                        "label": section.get("label", ""),
                        "icon": section_icon_svg,  # SVG HTML string
                        "is_active": section_active,
                        "items": filtered_items,
                    })
            
            # Update context - but don't overwrite values already set by routes
            context.setdefault("nav_items", filtered_nav)
            context.setdefault("user_role", user_role)
            context.setdefault("user_name", user_name)
            context.setdefault("current_path", current_path)
            context.setdefault("timezone", timezone)  # Only set if not already provided by route
            context.setdefault("debug_mode", debug_mode)
            context.setdefault("version", __version__)
            context.setdefault("request", request)
            
        except Exception as e:
            logger.error(f"Error in TemplateResponse: {e}")
            # Fallback
            context.setdefault("nav_items", [])
            context.setdefault("user_role", "viewer")
            context.setdefault("user_name", "User")
            context.setdefault("current_path", "/")
            context.setdefault("timezone", "UTC")
            context.setdefault("debug_mode", False)
            context.setdefault("version", __version__)
        
        return super().TemplateResponse(name, context, *args, **kwargs)

templates = TemplatesWithExtras(directory="templates")
