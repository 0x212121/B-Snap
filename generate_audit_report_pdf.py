#!/usr/bin/env python3
"""
B-SNAP IT Audit Report PDF Generator
Generates a professional PDF audit report for mining surveillance systems.
"""

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, 
    PageBreak, ListFlowable, ListItem, Image
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from datetime import datetime
import os

# Custom styles
def create_custom_styles():
    styles = getSampleStyleSheet()
    
    # Title style
    styles.add(ParagraphStyle(
        name='AuditTitle',
        fontSize=24,
        leading=30,
        alignment=TA_CENTER,
        spaceAfter=20,
        textColor=HexColor('#1a365d'),
        fontName='Helvetica-Bold'
    ))
    
    # Subtitle style
    styles.add(ParagraphStyle(
        name='AuditSubtitle',
        fontSize=14,
        leading=18,
        alignment=TA_CENTER,
        spaceAfter=30,
        textColor=HexColor('#4a5568'),
        fontName='Helvetica'
    ))
    
    # Section header style
    styles.add(ParagraphStyle(
        name='SectionHeader',
        fontSize=16,
        leading=20,
        spaceBefore=20,
        spaceAfter=12,
        textColor=HexColor('#2c5282'),
        fontName='Helvetica-Bold',
        borderPadding=5
    ))
    
    # Subsection header style
    styles.add(ParagraphStyle(
        name='SubsectionHeader',
        fontSize=13,
        leading=16,
        spaceBefore=15,
        spaceAfter=8,
        textColor=HexColor('#2d3748'),
        fontName='Helvetica-Bold'
    ))
    
    # Critical finding style
    styles.add(ParagraphStyle(
        name='CriticalFinding',
        fontSize=11,
        leading=14,
        spaceBefore=10,
        spaceAfter=8,
        textColor=HexColor('#c53030'),
        fontName='Helvetica-Bold'
    ))
    
    # Medium finding style
    styles.add(ParagraphStyle(
        name='MediumFinding',
        fontSize=11,
        leading=14,
        spaceBefore=10,
        spaceAfter=8,
        textColor=HexColor('#c05621'),
        fontName='Helvetica-Bold'
    ))
    
    # Low finding style
    styles.add(ParagraphStyle(
        name='LowFinding',
        fontSize=11,
        leading=14,
        spaceBefore=10,
        spaceAfter=8,
        textColor=HexColor('#276749'),
        fontName='Helvetica-Bold'
    ))
    
    # Positive finding style
    styles.add(ParagraphStyle(
        name='PositiveFinding',
        fontSize=11,
        leading=14,
        spaceBefore=10,
        spaceAfter=8,
        textColor=HexColor('#2f855a'),
        fontName='Helvetica-Bold'
    ))
    
    # Body text style
    styles.add(ParagraphStyle(
        name='AuditBodyText',
        fontSize=10,
        leading=14,
        spaceBefore=6,
        spaceAfter=6,
        alignment=TA_JUSTIFY,
        fontName='Helvetica'
    ))
    
    # Table header style
    styles.add(ParagraphStyle(
        name='TableHeader',
        fontSize=10,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.white,
        fontName='Helvetica-Bold'
    ))
    
    # Table cell style
    styles.add(ParagraphStyle(
        name='TableCell',
        fontSize=9,
        leading=11,
        fontName='Helvetica'
    ))
    
    # Footer style
    styles.add(ParagraphStyle(
        name='Footer',
        fontSize=8,
        leading=10,
        alignment=TA_CENTER,
        textColor=HexColor('#718096'),
        fontName='Helvetica'
    ))
    
    return styles


def create_cover_page(elements, styles):
    """Create the cover page"""
    # Title
    elements.append(Spacer(1, 2*inch))
    elements.append(Paragraph("🔒 B-SNAP SYSTEM", styles['AuditTitle']))
    elements.append(Paragraph("IT AUDIT REPORT", styles['AuditTitle']))
    elements.append(Spacer(1, 0.5*inch))
    
    # Subtitle
    elements.append(Paragraph(
        "Comprehensive Security Assessment for Mining Surveillance Systems",
        styles['AuditSubtitle']
    ))
    elements.append(Spacer(1, 1*inch))
    
    # Info box
    info_data = [
        ['Audit Information', ''],
        ['Audit Date:', 'April 7, 2026'],
        ['System Version:', 'B-SNAP v1.16.0'],
        ['Auditor:', 'Senior IT Auditor - Mining Surveillance'],
        ['Classification:', 'CONFIDENTIAL'],
        ['Next Review:', 'Quarterly or after significant changes'],
    ]
    
    info_table = Table(info_data, colWidths=[2.5*inch, 3*inch])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#2c5282')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('SPAN', (0, 0), (-1, 0)),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor('#f7fafc')),
        ('GRID', (0, 0), (-1, -1), 1, HexColor('#e2e8f0')),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
    ]))
    
    elements.append(info_table)
    elements.append(PageBreak())


def create_executive_summary(elements, styles):
    """Create executive summary section"""
    elements.append(Paragraph("Executive Summary", styles['SectionHeader']))
    
    # Overall status table
    status_data = [
        ['Aspect', 'Status', 'Rating'],
        ['Overall Compliance', 'Partially Compliant', '🟡 MEDIUM RISK'],
        ['Data Integrity & Evidence Protection', 'Strong Controls', '🟢 COMPLIANT'],
        ['Access Control (RBAC)', 'Implemented with Gaps', '🟡 MEDIUM RISK'],
        ['Audit Trail', 'Strong', '🟢 COMPLIANT'],
        ['Data Security', 'Good with Exceptions', '🟡 MEDIUM RISK'],
        ['Mining Safety Classification', 'Implemented', '🟢 COMPLIANT'],
    ]
    
    status_table = Table(status_data, colWidths=[3*inch, 1.8*inch, 1.5*inch])
    status_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#2c5282')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor('#f7fafc')),
        ('GRID', (0, 0), (-1, -1), 1, HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    
    elements.append(status_table)
    elements.append(Spacer(1, 0.2*inch))
    
    elements.append(Paragraph(
        "B-SNAP demonstrates <b>strong security foundations</b> with several P0/P1/P2 controls already implemented "
        "(SHA-256 hashes, soft delete, AES-256 encryption, append-only audit logs). However, <b>medium-risk gaps remain</b> "
        "in group-based access controls, video file access security, and session management that require attention before "
        "deployment in critical mining operations.",
        styles['AuditBodyText']
    ))
    elements.append(PageBreak())


def create_critical_findings(elements, styles):
    """Create critical findings section"""
    elements.append(Paragraph("Critical Findings (High Risk)", styles['SectionHeader']))
    
    # CRIT-001
    elements.append(Paragraph("⚠️ CRIT-001: Video Files Served via Direct Static Access", styles['CriticalFinding']))
    elements.append(Paragraph("<b>Risk Level:</b> 🔴 HIGH", styles['AuditBodyText']))
    elements.append(Paragraph("<b>Location:</b> app/routes/videos.py, video_gallery.html", styles['AuditBodyText']))
    elements.append(Paragraph(
        "<b>Issue:</b> Videos are served via direct /static/videos/{path} URLs without authentication, "
        "bypassing audit trail and access controls.",
        styles['AuditBodyText']
    ))
    elements.append(Paragraph(
        "<b>Mining Risk:</b> Safety incident footage could be accessed, downloaded, or deleted without traceability. "
        "This breaks the chain of custody for critical evidence.",
        styles['AuditBodyText']
    ))
    elements.append(Paragraph(
        "<b>Recommendation:</b> Implement authenticated API endpoints for video serving (similar to P2-004 snapshot security) "
        "with comprehensive audit logging.",
        styles['AuditBodyText']
    ))
    elements.append(Spacer(1, 0.15*inch))
    
    # CRIT-002
    elements.append(Paragraph("⚠️ CRIT-002: Group-Based Access Control Inconsistencies", styles['CriticalFinding']))
    elements.append(Paragraph("<b>Risk Level:</b> 🔴 HIGH", styles['AuditBodyText']))
    elements.append(Paragraph("<b>Location:</b> Multiple routes (snap_gallery.py, videos.py)", styles['AuditBodyText']))
    elements.append(Paragraph(
        "<b>Issue:</b> Group filtering uses camera name matching (Snapshot.camera_group == user_group.name) "
        "instead of foreign key relationships. Renaming a camera group could grant unauthorized access.",
        styles['AuditBodyText']
    ))
    elements.append(Paragraph(
        "<b>Mining Risk:</b> Users from one department could access footage from restricted areas "
        "(explosives zones, hazardous areas) violating safety protocols.",
        styles['AuditBodyText']
    ))
    elements.append(Paragraph(
        "<b>Recommendation:</b> Use camera.group_id foreign key relationships for all access control decisions.",
        styles['AuditBodyText']
    ))
    elements.append(Spacer(1, 0.15*inch))
    
    # CRIT-003
    elements.append(Paragraph("⚠️ CRIT-003: Missing Input Validation on Camera Import", styles['CriticalFinding']))
    elements.append(Paragraph("<b>Risk Level:</b> 🟠 MEDIUM-HIGH", styles['AuditBodyText']))
    elements.append(Paragraph("<b>Location:</b> app/routes/cameras.py CSV upload", styles['AuditBodyText']))
    elements.append(Paragraph(
        "<b>Issue:</b> CSV import allows setting any safety_classification without proper validation; "
        "passwords imported in plain text without encryption enforcement.",
        styles['AuditBodyText']
    ))
    elements.append(Paragraph(
        "<b>Mining Risk:</b> Safety-critical cameras misclassified as 'low' priority could be excluded "
        "from incident investigations, compromising worker safety.",
        styles['AuditBodyText']
    ))
    elements.append(Paragraph(
        "<b>Recommendation:</b> Add strict validation and require admin confirmation for 'critical' "
        "classification changes during CSV import.",
        styles['AuditBodyText']
    ))
    elements.append(PageBreak())


def create_medium_findings(elements, styles):
    """Create medium findings section"""
    elements.append(Paragraph("Medium Findings (Medium Risk)", styles['SectionHeader']))
    
    findings = [
        {
            'title': '🟡 MED-001: Session Cookie Configuration Vulnerabilities',
            'risk': 'MEDIUM',
            'location': 'app/routes/auth.py',
            'issue': 'Session token max_age is 24 hours but expires_at is 30 days - inconsistency in session management.',
            'mining_risk': 'Stale sessions could allow continued access after user termination.',
            'recommendation': 'Align cookie max_age with token expiration; implement shorter session timeouts for mining environments.'
        },
        {
            'title': '🟡 MED-002: API Tokens Lack Expiration Enforcement',
            'risk': 'MEDIUM',
            'location': 'app/routes/auth.py',
            'issue': 'API tokens without expires_at are accepted indefinitely.',
            'mining_risk': 'Compromised API tokens could provide persistent unauthorized access to CCTV footage.',
            'recommendation': 'Require expiration on all API tokens; implement token rotation policy.'
        },
        {
            'title': '🟡 MED-003: No Retention Policy Enforcement',
            'risk': 'MEDIUM',
            'location': 'Configuration only',
            'issue': 'MAX_SNAPSHOT_AGE_DAYS and MAX_VIDEO_AGE_DAYS exist but no automated enforcement visible.',
            'mining_risk': 'Evidence retention beyond legal requirements could create liability.',
            'recommendation': 'Implement automated retention policy enforcement with legal hold exceptions.'
        },
        {
            'title': '🟡 MED-004: Missing Geo-Location Validation',
            'risk': 'MEDIUM',
            'location': 'app/models/camera.py',
            'issue': 'GPS coordinates stored but not validated for valid ranges.',
            'mining_risk': 'Invalid coordinates could misplace cameras on maps, directing emergency response to wrong locations.',
            'recommendation': 'Add validation for valid GPS coordinate ranges.'
        }
    ]
    
    for finding in findings:
        elements.append(Paragraph(finding['title'], styles['MediumFinding']))
        elements.append(Paragraph(f"<b>Location:</b> {finding['location']}", styles['AuditBodyText']))
        elements.append(Paragraph(f"<b>Issue:</b> {finding['issue']}", styles['AuditBodyText']))
        elements.append(Paragraph(f"<b>Mining Risk:</b> {finding['mining_risk']}", styles['AuditBodyText']))
        elements.append(Paragraph(f"<b>Recommendation:</b> {finding['recommendation']}", styles['AuditBodyText']))
        elements.append(Spacer(1, 0.1*inch))
    
    elements.append(PageBreak())


def create_positive_controls(elements, styles):
    """Create positive security controls section"""
    elements.append(Paragraph("Positive Security Controls (Implemented)", styles['SectionHeader']))
    
    controls = [
        {
            'code': 'P0-001',
            'title': 'SHA-256 File Hashing',
            'status': '✅ IMPLEMENTED',
            'evidence': 'file_hash column in snapshots/videos; calculate_hash() and verify_integrity() methods',
            'compliance': 'ISO 27001 A.12.3, COBIT BAI03'
        },
        {
            'code': 'P0-002',
            'title': 'Soft Delete with Trash Management',
            'status': '✅ IMPLEMENTED',
            'evidence': 'deleted_at column; soft_delete() method; admin trash page; purge requires admin',
            'compliance': 'ISO 27001 A.12.4, COBIT APO12'
        },
        {
            'code': 'P1-001',
            'title': 'AES-256-GCM Password Encryption',
            'status': '✅ IMPLEMENTED',
            'evidence': 'app/utils/encryption.py; camera/nvr password encryption with environment key',
            'compliance': 'ISO 27001 A.10.1'
        },
        {
            'code': 'P1-002',
            'title': 'Password Removed from API Responses',
            'status': '✅ IMPLEMENTED',
            'evidence': 'Camera API excludes password; separate admin-only endpoint for password viewing',
            'compliance': 'ISO 27001 A.9.4'
        },
        {
            'code': 'P2-001',
            'title': 'Append-Only Audit Log',
            'status': '✅ IMPLEMENTED',
            'evidence': 'PostgreSQL triggers preventing UPDATE/DELETE on audit_logs table',
            'compliance': 'ISO 27001 A.12.4.4, COBIT MEA01'
        },
        {
            'code': 'P2-002',
            'title': 'Safety Classification',
            'status': '✅ IMPLEMENTED',
            'evidence': 'safety_classification column (critical/standard/low) for mining operations',
            'compliance': 'ISO 45001'
        },
        {
            'code': 'P2-003',
            'title': 'Retention Hold (Legal Hold)',
            'status': '✅ IMPLEMENTED',
            'evidence': 'retention_hold, retention_hold_reason, retention_hold_by columns',
            'compliance': 'Legal/Evidence retention requirements'
        },
        {
            'code': 'P2-004',
            'title': 'Secure Snapshot Serving',
            'status': '✅ IMPLEMENTED',
            'evidence': '/static/snapshots/* blocked; authenticated API endpoints with audit logging',
            'compliance': 'ISO 27001 A.9.4'
        }
    ]
    
    for control in controls:
        elements.append(Paragraph(f"✅ {control['code']}: {control['title']}", styles['PositiveFinding']))
        elements.append(Paragraph(f"<b>Status:</b> {control['status']}", styles['AuditBodyText']))
        elements.append(Paragraph(f"<b>Evidence:</b> {control['evidence']}", styles['AuditBodyText']))
        elements.append(Paragraph(f"<b>Compliance:</b> {control['compliance']}", styles['AuditBodyText']))
        elements.append(Spacer(1, 0.08*inch))
    
    elements.append(PageBreak())


def create_compliance_mapping(elements, styles):
    """Create compliance mapping table"""
    elements.append(Paragraph("Compliance Mapping", styles['SectionHeader']))
    
    compliance_data = [
        ['Finding', 'ISO 27001', 'ISO 45001', 'COBIT', 'Impact'],
        ['CRIT-001: Video direct access', 'A.9.4.1, A.12.4', '6.1.2', 'BAI03', '🔴 Evidence integrity'],
        ['CRIT-002: Group access bypass', 'A.9.1.2', '8.1.3', 'DSS05', '🔴 Unauthorized access'],
        ['CRIT-003: CSV validation gaps', 'A.12.2.1', '8.1.1', 'BAI03', '🟠 Misclassification'],
        ['MED-001: Session inconsistency', 'A.9.4.2', '-', 'DSS05', '🟡 Stale access'],
        ['MED-002: Token no expiration', 'A.9.4.1', '-', 'DSS05', '🟡 Persistent access'],
        ['MED-003: No retention enforcement', 'A.12.3.1', '6.1.3', 'APO12', '🟡 Compliance violation'],
        ['P0-001: SHA-256 hashes', 'A.10.1.2', '-', 'BAI03', '✅ Evidence integrity'],
        ['P0-002: Soft delete', 'A.12.4.1', '-', 'APO12', '✅ Evidence preservation'],
        ['P1-001: AES-256 encryption', 'A.10.1.1', '-', 'DSS05', '✅ Credential protection'],
        ['P2-001: Append-only audit', 'A.12.4.4', '-', 'MEA01', '✅ Audit integrity'],
        ['P2-002: Safety classification', 'A.12.1.2', '6.1.2, 8.1.3', 'EDM01', '✅ Safety priority'],
    ]
    
    comp_table = Table(compliance_data, colWidths=[2.2*inch, 1.1*inch, 0.9*inch, 0.7*inch, 1.3*inch])
    comp_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#2c5282')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor('#f7fafc')),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
    ]))
    
    elements.append(comp_table)
    elements.append(PageBreak())


def create_recommendations(elements, styles):
    """Create recommendations section"""
    elements.append(Paragraph("Recommendations", styles['SectionHeader']))
    
    # Immediate Actions
    elements.append(Paragraph("Immediate Actions (0-30 days)", styles['SubsectionHeader']))
    
    immediate_data = [
        ['Priority', 'Action', 'Owner', 'Effort'],
        ['🔴 P0', 'Implement secure video serving (mirror P2-004)', 'Dev Team', 'Medium'],
        ['🔴 P0', 'Fix group-based access control to use foreign keys', 'Dev Team', 'Medium'],
        ['🟠 P1', 'Align session cookie max_age with token expiration', 'Dev Team', 'Low'],
    ]
    
    imm_table = Table(immediate_data, colWidths=[0.8*inch, 3.5*inch, 1*inch, 0.8*inch])
    imm_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#c53030')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor('#fff5f5')),
        ('GRID', (0, 0), (-1, -1), 1, HexColor('#feb2b2')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    
    elements.append(imm_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # Short-term Actions
    elements.append(Paragraph("Short-term Actions (1-3 months)", styles['SubsectionHeader']))
    
    short_data = [
        ['Priority', 'Action', 'Owner', 'Effort'],
        ['🟠 P1', 'Enforce API token expiration; implement rotation', 'Dev Team', 'Medium'],
        ['🟠 P1', 'Implement automated retention policy with legal hold', 'Dev Team', 'Medium'],
        ['🟠 P1', 'Add CSV import validation for safety classifications', 'Dev Team', 'Low'],
    ]
    
    short_table = Table(short_data, colWidths=[0.8*inch, 3.5*inch, 1*inch, 0.8*inch])
    short_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#c05621')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor('#fffaf0')),
        ('GRID', (0, 0), (-1, -1), 1, HexColor('#fbd38d')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    
    elements.append(short_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # Long-term Actions
    elements.append(Paragraph("Long-term Improvements (3-6 months)", styles['SubsectionHeader']))
    
    long_data = [
        ['Priority', 'Action', 'Owner', 'Effort'],
        ['🟢 P2', 'Implement rate limiting on public endpoints', 'Dev Team', 'Medium'],
        ['🟢 P2', 'Add GPS coordinate validation', 'Dev Team', 'Low'],
        ['🟢 P2', 'Consider blockchain anchoring for audit logs (P3-002)', 'Security Team', 'High'],
    ]
    
    long_table = Table(long_data, colWidths=[0.8*inch, 3.5*inch, 1.2*inch, 0.8*inch])
    long_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#276749')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor('#f0fff4')),
        ('GRID', (0, 0), (-1, -1), 1, HexColor('#9ae6b4')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    
    elements.append(long_table)
    elements.append(PageBreak())


def create_risk_assessment(elements, styles):
    """Create overall risk assessment"""
    elements.append(Paragraph("Overall Risk Assessment", styles['SectionHeader']))
    
    risk_data = [
        ['Category', 'Rating', 'Rationale'],
        ['Evidence Integrity', '🟢 LOW RISK', 'SHA-256 hashes, soft delete, and append-only audit logs provide strong evidence protection'],
        ['Access Control', '🟡 MEDIUM RISK', 'RBAC implemented but group filtering has weaknesses; video access not fully secured'],
        ['Audit Capability', '🟢 LOW RISK', 'Comprehensive audit logging with database-level protection'],
        ['Mining Safety', '🟡 MEDIUM RISK', 'Safety classification exists but access control gaps could expose critical footage'],
        ['Compliance Readiness', '🟡 MEDIUM RISK', 'Good foundation but gaps in video security and retention enforcement'],
    ]
    
    risk_table = Table(risk_data, colWidths=[2*inch, 1.5*inch, 3.5*inch])
    risk_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#2c5282')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor('#f7fafc')),
        ('GRID', (0, 0), (-1, -1), 1, HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    
    elements.append(risk_table)
    elements.append(Spacer(1, 0.3*inch))
    
    elements.append(Paragraph("Final Recommendation", styles['SubsectionHeader']))
    elements.append(Paragraph(
        "<b>B-SNAP is suitable for mining operations with conditions:</b>",
        styles['AuditBodyText']
    ))
    
    recommendations = [
        "Proceed with deployment after fixing CRIT-001 (video security) and CRIT-002 (group access)",
        "Implement mandatory 2FA for all admin accounts (already supported)",
        "Conduct penetration testing on video endpoints before production",
        "Establish incident response procedures for evidence tampering detection",
        "Schedule quarterly security reviews (P3-002 and other improvements)"
    ]
    
    for i, rec in enumerate(recommendations, 1):
        elements.append(Paragraph(f"{i}. {rec}", styles['AuditBodyText']))
    
    elements.append(Spacer(1, 0.3*inch))
    
    # Document control
    elements.append(Paragraph("Document Control", styles['SubsectionHeader']))
    control_data = [
        ['Audit Date:', 'April 7, 2026'],
        ['Auditor:', 'Senior IT Auditor - Mining Surveillance'],
        ['System Version:', 'B-SNAP v1.16.0'],
        ['Classification:', 'CONFIDENTIAL'],
        ['Next Review:', 'Quarterly or after significant changes'],
    ]
    
    control_table = Table(control_data, colWidths=[2*inch, 4*inch])
    control_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor('#edf2f7')),
        ('GRID', (0, 0), (-1, -1), 1, HexColor('#cbd5e0')),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    
    elements.append(control_table)


def generate_pdf(output_path="B-SNAP_IT_Audit_Report.pdf"):
    """Generate the complete PDF report"""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=0.75*inch,
        leftMargin=0.75*inch,
        topMargin=0.75*inch,
        bottomMargin=0.75*inch
    )
    
    styles = create_custom_styles()
    elements = []
    
    # Build the report
    create_cover_page(elements, styles)
    create_executive_summary(elements, styles)
    create_critical_findings(elements, styles)
    create_medium_findings(elements, styles)
    create_positive_controls(elements, styles)
    create_compliance_mapping(elements, styles)
    create_recommendations(elements, styles)
    create_risk_assessment(elements, styles)
    
    # Build PDF
    doc.build(elements)
    print(f"[OK] PDF report generated successfully: {output_path}")
    print(f"[INFO] Full path: {os.path.abspath(output_path)}")
    return output_path


if __name__ == "__main__":
    generate_pdf()
