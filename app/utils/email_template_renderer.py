"""
Email Template Renderer

Renders email templates with Jinja2 templating engine.
"""
import logging
from typing import Optional
from jinja2 import Template, UndefinedError
from sqlalchemy.orm import Session

from app.models.email_template import EmailTemplate, get_default_template

logger = logging.getLogger("email_template_renderer")


def render_template(template_type: str, context: dict, db: Session) -> tuple[str, str, str]:
    """
    Render email template with given context.
    
    Args:
        template_type: Type of template (tamper_alert, recovery_alert, offline_alert)
        context: Dictionary of variables to render
        db: Database session
        
    Returns:
        Tuple of (subject, plain_body, html_body)
    """
    # Get template from database or use default
    template = db.query(EmailTemplate).filter_by(template_type=template_type).first()
    
    if template:
        subject_template = template.subject
        plain_template = template.plain_body
        html_template = template.html_body
    else:
        # Use default template
        default = get_default_template(template_type)
        subject_template = default["subject"]
        plain_template = default["plain_body"]
        html_template = default["html_body"]
    
    try:
        # Render subject
        subject = Template(subject_template).render(**context)
        
        # Render plain body
        plain_body = Template(plain_template).render(**context)
        
        # Render HTML body
        html_body = Template(html_template).render(**context)
        
        return subject, plain_body, html_body
        
    except UndefinedError as e:
        logger.error(f"Template rendering error for {template_type}: {e}")
        # Fallback to default template if custom template has errors
        default = get_default_template(template_type)
        subject = Template(default["subject"]).render(**context)
        plain_body = Template(default["plain_body"]).render(**context)
        html_body = Template(default["html_body"]).render(**context)
        return subject, plain_body, html_body
    except Exception as e:
        logger.exception(f"Unexpected error rendering template {template_type}: {e}")
        # Return simple fallback
        return (
            f"CCTV Alert - {context.get('camera_name', 'Unknown')}",
            f"Alert for camera {context.get('camera_name', 'Unknown')}",
            f"<p>Alert for camera {context.get('camera_name', 'Unknown')}</p>"
        )


def preview_template(template_type: str, subject: str, plain_body: str, html_body: str, 
                     context: dict) -> tuple[str, str, str]:
    """
    Preview template with sample context without saving.
    
    Args:
        template_type: Type of template
        subject: Subject template string
        plain_body: Plain body template string
        html_body: HTML body template string
        context: Sample context data
        
    Returns:
        Tuple of (rendered_subject, rendered_plain, rendered_html)
    """
    try:
        rendered_subject = Template(subject).render(**context)
        rendered_plain = Template(plain_body).render(**context)
        rendered_html = Template(html_body).render(**context)
        return rendered_subject, rendered_plain, rendered_html
    except Exception as e:
        logger.error(f"Preview rendering error: {e}")
        raise


def get_sample_context(template_type: str) -> dict:
    """
    Get sample context data for template preview.
    
    Args:
        template_type: Type of template
        
    Returns:
        Dictionary with sample data
    """
    base_context = {
        "camera_name": "CAM-001-FrontGate",
        "camera_ip": "192.168.1.100",
        "camera_group": "Security Division",
        "asset_no": "AST-CCTV-2024-001",
        "location": "Front Gate Main Entrance",
        "latitude": "-0.7893",
        "longitude": "117.9213",
        "has_snapshot": True,
    }
    
    if template_type == "tamper_alert":
        base_context.update({
            "reason": "blur",
            "incident_time": "01/04/2026 14:30:45",
        })
    elif template_type == "recovery_alert":
        base_context.update({
            "last_reason": "blur",
            "recovery_time": "01/04/2026 15:15:22",
            "snapshot_time": "01/04/2026 15:15:20",
        })
    elif template_type == "offline_alert":
        base_context.update({
            "incident_time": "01/04/2026 14:30:45",
            "offline_duration": "35",
            "snapshot_time": "01/04/2026 14:25:10",
        })
    
    return base_context
