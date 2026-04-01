"""
Email Templates Routes

API endpoints for managing email notification templates.
"""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import User
from app.models.email_template import (
    EmailTemplate, 
    get_default_template, 
    get_template_variables,
    TEMPLATE_VARIABLES
)
from app.utils.template_helper import templates
from app.utils.email_template_renderer import render_template, preview_template, get_sample_context
from app.routes.auth import admin_access_required
from app.utils.audit_logger import log_audit

router = APIRouter(tags=["Email Templates"])


class TemplatePreviewRequest(BaseModel):
    template_type: str
    subject: str
    plain_body: str
    html_body: str


@router.get("/email-templates", response_class=HTMLResponse)
async def email_templates_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Render email templates management page."""
    # Get all templates from database
    templates_db = db.query(EmailTemplate).all()
    templates_dict = {t.template_type: t for t in templates_db}
    
    # Ensure all template types exist (use defaults if not in DB)
    template_types = ["tamper_alert", "recovery_alert", "offline_alert"]
    template_data = []
    
    for t_type in template_types:
        if t_type in templates_dict:
            t = templates_dict[t_type]
            template_data.append({
                "type": t_type,
                "subject": t.subject,
                "plain_body": t.plain_body,
                "html_body": t.html_body,
                "updated_at": t.updated_at,
                "is_custom": True
            })
        else:
            default = get_default_template(t_type)
            template_data.append({
                "type": t_type,
                "subject": default["subject"],
                "plain_body": default["plain_body"],
                "html_body": default["html_body"],
                "updated_at": None,
                "is_custom": False
            })
    
    # Get variables for each template type
    variables = TEMPLATE_VARIABLES
    
    return templates.TemplateResponse("email_templates.html", {
        "request": request,
        "templates": template_data,
        "variables": variables
    })


@router.get("/api/email-templates/{template_type}")
async def get_template(
    template_type: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Get a specific template (custom or default)."""
    template = db.query(EmailTemplate).filter_by(template_type=template_type).first()
    
    if template:
        return {
            "template_type": template.template_type,
            "subject": template.subject,
            "plain_body": template.plain_body,
            "html_body": template.html_body,
            "is_custom": True,
            "updated_at": template.updated_at.isoformat() if template.updated_at else None
        }
    else:
        # Return default template
        default = get_default_template(template_type)
        return {
            "template_type": template_type,
            "subject": default["subject"],
            "plain_body": default["plain_body"],
            "html_body": default["html_body"],
            "is_custom": False,
            "updated_at": None
        }


@router.post("/api/email-templates/{template_type}/save")
async def save_template(
    request: Request,
    template_type: str,
    subject: str = Form(...),
    plain_body: str = Form(...),
    html_body: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Save or update an email template."""
    # Validate template type
    valid_types = ["tamper_alert", "recovery_alert", "offline_alert"]
    if template_type not in valid_types:
        return JSONResponse(
            status_code=400,
            content={"message": f"Invalid template type. Must be one of: {', '.join(valid_types)}"}
        )
    
    # Validate required fields
    if not subject.strip():
        return JSONResponse(status_code=400, content={"message": "Subject is required"})
    if not plain_body.strip():
        return JSONResponse(status_code=400, content={"message": "Plain body is required"})
    if not html_body.strip():
        return JSONResponse(status_code=400, content={"message": "HTML body is required"})
    
    try:
        # Test render with sample context to validate template syntax
        sample_context = get_sample_context(template_type)
        preview_template(template_type, subject, plain_body, html_body, sample_context)
        
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"message": f"Template syntax error: {str(e)}"}
        )
    
    # Save to database
    template = db.query(EmailTemplate).filter_by(template_type=template_type).first()
    
    if template:
        template.subject = subject
        template.plain_body = plain_body
        template.html_body = html_body
    else:
        template = EmailTemplate(
            template_type=template_type,
            subject=subject,
            plain_body=plain_body,
            html_body=html_body
        )
        db.add(template)
    
    db.commit()
    
    # Log the action
    log_audit(
        db=db,
        user=current_admin.username,
        action="email_template_update",
        target=f"template:{template_type}",
        ip=request.client.host if request.client else None,
        extra={"subject": subject}
    )
    
    return JSONResponse(
        status_code=200,
        content={"message": f"Template '{template_type}' saved successfully"}
    )


@router.post("/api/email-templates/{template_type}/preview")
async def preview_template_endpoint(
    template_type: str,
    data: TemplatePreviewRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Preview template with sample data."""
    try:
        sample_context = get_sample_context(template_type)
        subject, plain_body, html_body = preview_template(
            template_type,
            data.subject,
            data.plain_body,
            data.html_body,
            sample_context
        )
        
        return {
            "subject": subject,
            "plain_body": plain_body,
            "html_body": html_body
        }
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"message": f"Preview error: {str(e)}"}
        )


@router.post("/api/email-templates/{template_type}/reset")
async def reset_template(
    request: Request,
    template_type: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Reset template to default (delete custom template)."""
    template = db.query(EmailTemplate).filter_by(template_type=template_type).first()
    
    if template:
        db.delete(template)
        db.commit()
        
        # Log the action
        log_audit(
            db=db,
            user=current_admin.username,
            action="email_template_reset",
            target=f"template:{template_type}",
            ip=request.client.host if request.client else None
        )
        
        return JSONResponse(
            status_code=200,
            content={"message": f"Template '{template_type}' reset to default"}
        )
    else:
        return JSONResponse(
            status_code=404,
            content={"message": f"Template '{template_type}' not found"}
        )


@router.get("/api/email-templates/{template_type}/variables")
async def get_variables(
    template_type: str,
    current_admin: User = Depends(admin_access_required)
):
    """Get available variables for a template type."""
    variables = get_template_variables(template_type)
    return {"template_type": template_type, "variables": variables}
