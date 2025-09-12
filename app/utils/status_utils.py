# status_utils.py
def get_status_classes(status: str) -> str:
    mapping = {
        "Active": "bg-teal-100 text-teal-800",
        "Restricted": "bg-orange-100 text-orange-800",
        "Deactivated": "bg-slate-200 text-slate-800",
        "Maintenance": "bg-amber-100 text-amber-800",
    }
    return mapping.get(status, "bg-zinc-100 text-zinc-600")
