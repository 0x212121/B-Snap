#!/usr/bin/env python3
"""
B-SNAP IT Audit Report PDF Generator
Converts the markdown audit report to a professionally formatted PDF
"""

from fpdf import FPDF
from datetime import datetime, timezone
import os

class AuditReportPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=15)
        
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font('Arial', 'B', 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, 'B-SNAP CCTV System - IT Audit Report - CONFIDENTIAL', 0, 0, 'L')
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'R')
        self.ln(15)
        
    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, 'Mining Surveillance Systems Audit - 2026', 0, 0, 'C')
        
    def cover_page(self):
        self.add_page()
        self.set_draw_color(200, 0, 0)
        self.set_line_width(2)
        self.rect(10, 10, 190, 277)
        
        self.set_fill_color(200, 0, 0)
        self.rect(10, 30, 190, 15, 'F')
        self.set_font('Arial', 'B', 14)
        self.set_text_color(255, 255, 255)
        self.cell(0, 20, 'CONFIDENTIAL', 0, 1, 'C')
        
        self.ln(30)
        self.set_font('Arial', 'B', 24)
        self.set_text_color(0, 51, 102)
        self.cell(0, 15, 'B-SNAP CCTV SYSTEM', 0, 1, 'C')
        self.cell(0, 15, 'IT AUDIT REPORT', 0, 1, 'C')
        
        self.ln(10)
        self.set_font('Arial', 'B', 14)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, 'Mining Surveillance & Safety-Critical Environment Assessment', 0, 1, 'C')
        
        self.ln(20)
        self.set_fill_color(255, 200, 0)
        self.rect(50, 160, 110, 40, 'F')
        self.set_font('Arial', 'B', 16)
        self.set_text_color(0, 0, 0)
        self.set_y(165)
        self.cell(0, 10, 'OVERALL STATUS', 0, 1, 'C')
        self.set_font('Arial', 'B', 20)
        self.set_text_color(200, 0, 0)
        self.cell(0, 15, 'PARTIAL / HIGH RISK', 0, 1, 'C')
        
        self.ln(50)
        self.set_font('Arial', '', 11)
        self.set_text_color(0, 0, 0)
        self.cell(0, 8, f'Audit Date: {datetime.now(timezone.utc).strftime("%B %d, %Y")}', 0, 1, 'C')
        self.cell(0, 8, 'Auditor: Senior IT Auditor - Mining Surveillance Systems', 0, 1, 'C')
        self.cell(0, 8, 'System: B-SNAP (CCTV Management System)', 0, 1, 'C')
        self.cell(0, 8, 'Classification: CONFIDENTIAL', 0, 1, 'C')
        
        self.ln(30)
        self.set_font('Arial', 'I', 9)
        self.set_text_color(150, 0, 0)
        self.multi_cell(0, 5, 'This document contains confidential security assessment findings. '
                              'Distribution is restricted to authorized personnel only.', 0, 'C')
    
    def section_title(self, title, level=1):
        if level == 1:
            self.set_font('Arial', 'B', 16)
            self.set_text_color(0, 51, 102)
            self.set_fill_color(230, 240, 250)
            self.cell(0, 12, title, 0, 1, 'L', True)
            self.ln(3)
        elif level == 2:
            self.set_font('Arial', 'B', 13)
            self.set_text_color(0, 80, 120)
            self.cell(0, 10, title, 0, 1, 'L')
        else:
            self.set_font('Arial', 'B', 11)
            self.set_text_color(0, 0, 0)
            self.cell(0, 8, title, 0, 1, 'L')
            
    def body_text(self, text, bold=False):
        self.set_font('Arial', 'B' if bold else '', 10)
        self.set_text_color(0, 0, 0)
        self.multi_cell(0, 6, text)
        self.ln(2)
        
    def finding_box(self, title, risk_level, description, impact, location=""):
        risk_colors = {
            'CRITICAL': (200, 0, 0),
            'HIGH': (255, 100, 0),
            'MEDIUM': (255, 180, 0),
            'LOW': (0, 150, 0)
        }
        color = risk_colors.get(risk_level.upper(), (100, 100, 100))
        
        self.set_fill_color(*color)
        self.set_text_color(255, 255, 255)
        self.set_font('Arial', 'B', 11)
        self.cell(0, 8, f'  {title[:80]}', 0, 1, 'L', True)
        
        self.set_fill_color(255, 255, 255)
        self.set_text_color(*color)
        self.set_font('Arial', 'B', 10)
        self.cell(0, 6, f'  Risk Level: {risk_level}', 0, 1, 'L')
        
        if location:
            self.set_text_color(100, 100, 100)
            self.set_font('Arial', 'I', 9)
            self.cell(0, 5, f'  Location: {location}', 0, 1, 'L')
        
        self.ln(2)
        self.set_text_color(0, 0, 0)
        self.set_font('Arial', '', 10)
        self.multi_cell(0, 5, f'  {description}')
        self.ln(1)
        
        self.set_font('Arial', 'B', 10)
        self.set_text_color(150, 0, 0)
        self.cell(0, 6, '  Impact:', 0, 1, 'L')
        self.set_text_color(0, 0, 0)
        self.set_font('Arial', '', 10)
        self.multi_cell(0, 5, f'  {impact}')
        self.ln(5)
        
    def recommendation_row(self, priority, action, effort):
        colors = {
            'P0': (200, 0, 0),
            'P1': (255, 100, 0),
            'P2': (255, 180, 0),
            'P3': (0, 100, 200)
        }
        color = colors.get(priority, (100, 100, 100))
        
        self.set_fill_color(*color)
        self.set_text_color(255, 255, 255)
        self.set_font('Arial', 'B', 10)
        self.cell(20, 8, priority, 1, 0, 'C', True)
        
        self.set_fill_color(255, 255, 255)
        self.set_text_color(0, 0, 0)
        self.set_font('Arial', '', 9)
        self.cell(120, 8, action[:65], 1, 0, 'L')
        self.cell(40, 8, effort, 1, 1, 'C')


def generate_pdf():
    pdf = AuditReportPDF()
    
    # Cover Page
    pdf.cover_page()
    
    # Executive Summary
    pdf.add_page()
    pdf.section_title('Executive Summary', 1)
    pdf.body_text('This audit assessed the B-SNAP CCTV management system for security, auditability, '
                  'and compliance in a mining operations context where CCTV data serves as critical '
                  'evidence and safety monitoring data.')
    
    pdf.section_title('Assessment Summary', 2)
    summary_data = [
        ('CCTV Data Integrity', 'PARTIAL', 'HIGH RISK'),
        ('Access Control (RBAC)', 'COMPLIANT', 'Medium'),
        ('Audit Trail', 'PARTIAL', 'HIGH RISK'),
        ('Data Security', 'PARTIAL', 'Medium'),
        ('Data Classification', 'NON-COMPLIANT', 'HIGH RISK'),
        ('System Reliability', 'COMPLIANT', 'Low'),
        ('Database Design', 'PARTIAL', 'Medium'),
    ]
    
    pdf.set_fill_color(0, 51, 102)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(80, 8, 'Assessment Area', 1, 0, 'C', True)
    pdf.cell(50, 8, 'Status', 1, 0, 'C', True)
    pdf.cell(50, 8, 'Risk Level', 1, 1, 'C', True)
    
    for area, status, risk in summary_data:
        pdf.set_font('Arial', '', 9)
        if 'HIGH' in risk:
            pdf.set_text_color(200, 0, 0)
        elif 'Medium' in risk:
            pdf.set_text_color(255, 150, 0)
        else:
            pdf.set_text_color(0, 128, 0)
        pdf.cell(80, 7, area, 1, 0, 'L')
        pdf.cell(50, 7, status, 1, 0, 'C')
        pdf.cell(50, 7, risk, 1, 1, 'C')
    
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(0, 10, 'Overall Compliance Level: PARTIAL / HIGH RISK', 0, 1, 'C')
    pdf.ln(5)
    pdf.set_font('Arial', '', 10)
    pdf.multi_cell(0, 6, 'The B-SNAP system demonstrates adequate foundational security with mandatory 2FA, '
                          'role-based access control, and group-based camera restrictions. However, critical '
                          'gaps exist in evidence protection and audit trail integrity that pose significant '
                          'risks for mining operations.')
    
    # Critical Findings
    pdf.add_page()
    pdf.section_title('Critical Findings (High Risk)', 1)
    
    pdf.finding_box(
        '1. CRITICAL: No Evidence Integrity Verification',
        'CRITICAL',
        'Snapshots and videos are stored without cryptographic hash or checksum. No mechanism exists '
        'to detect tampering with stored image/video files. No digital signature or chain of custody tracking.',
        'Evidence inadmissibility in legal proceedings. Cannot prove footage has not been altered. '
        'Safety incident investigations compromised. Regulatory non-compliance.',
        'app/models/snapshot.py, app/models/video.py'
    )
    
    pdf.finding_box(
        '2. CRITICAL: Audit Logs Lack Tamper Protection',
        'CRITICAL',
        'Audit logs are stored in a standard database table with no immutability controls. '
        'No foreign key relationships ensure referential integrity. Logs can be modified or deleted by DB admins.',
        'Compliance violations. Cannot prove audit trail integrity to regulators. '
        'Forensic investigation failure. Accountability gaps.',
        'app/models/audit_log.py'
    )
    
    pdf.finding_box(
        '3. CRITICAL: CCTV Footage Can Be Deleted Without Trace',
        'CRITICAL',
        'Operators can permanently delete snapshots with no recovery mechanism. '
        'Videos can be deleted without proper permission checks. No retention hold capability.',
        'Evidence destruction risk. Legal liability for spoliation of evidence. '
        'Regulatory penalties. Mining regulators require retention of safety footage.',
        'app/routes/snap_gallery.py, app/utils/snapshot_service.py'
    )
    
    pdf.add_page()
    pdf.finding_box(
        '4. CRITICAL: No Safety Classification for Cameras/Footage',
        'CRITICAL',
        'No classification of cameras by safety criticality. No distinction between general monitoring '
        'vs safety-critical areas (hazardous zones, explosives storage, haul roads).',
        'ISO 45001 non-compliance. Cannot demonstrate safety monitoring prioritization. '
        'Incident response delays. Loss of safety-critical footage may go undetected.',
        'app/models/camera.py'
    )
    
    pdf.finding_box(
        '5. HIGH: Camera Credentials Stored in Plain Text',
        'HIGH',
        'Camera passwords stored in database without encryption. Credentials exposed in API responses.',
        'Privilege escalation. Database access grants camera network access. '
        'Lateral movement risk. Compliance violation.',
        'app/models/camera.py'
    )
    
    # Medium/Low Findings
    pdf.add_page()
    pdf.section_title('Medium / Low Findings', 1)
    
    pdf.section_title('6. MEDIUM: Incomplete Audit Trail for CCTV Access', 2)
    pdf.body_text('Snapshot retrieval is logged but does not record which specific snapshot was accessed. '
                  'No video access logging with file identification.')
    
    pdf.section_title('7. MEDIUM: Static File URLs Predictable', 2)
    pdf.body_text('Snapshots served via predictable URLs. Direct static file access may bypass audit logging.')
    
    pdf.section_title('8. LOW: Session Cookie Security', 2)
    pdf.body_text('Cookie secure=False in some authentication code paths. Should enforce HTTPS in production.')
    
    # Compliance Mapping
    pdf.add_page()
    pdf.section_title('Compliance Mapping', 1)
    pdf.body_text('Mapping of findings to relevant compliance frameworks:')
    pdf.ln(3)
    
    headers = ['Finding', 'ISO 27001', 'ISO 45001', 'COBIT', 'Impact']
    widths = [60, 30, 30, 35, 45]
    
    pdf.set_fill_color(0, 51, 102)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Arial', 'B', 8)
    for header, width in zip(headers, widths):
        pdf.cell(width, 8, header, 1, 0, 'C', True)
    pdf.ln()
    
    compliance_data = [
        ('1. No evidence integrity', 'A.8.2', '6.1.2', 'BAI03.03', 'Legal evidence'),
        ('2. Audit logs tamperable', 'A.12.4', '9.1.1', 'MEA01.04', 'Audit failure'),
        ('3. Footage deletion', 'A.8.3', '6.1.4', 'BAI03.04', 'Evidence loss'),
        ('4. No safety classification', 'A.5.12', '6.1.2', 'APO12.02', 'Safety gaps'),
        ('5. Plain text credentials', 'A.9.4', 'N/A', 'DSS05.05', 'Privilege escalation'),
        ('6. Incomplete audit trail', 'A.12.4', '9.1.1', 'MEA01.04', 'Accountability'),
        ('7. Predictable URLs', 'A.9.4', 'N/A', 'DSS05.03', 'Access risk'),
    ]
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Arial', '', 8)
    for i, row in enumerate(compliance_data):
        pdf.set_fill_color(240, 240, 240) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
        for cell, width in zip(row, widths):
            pdf.cell(width, 6, cell, 1, 0, 'L', True)
        pdf.ln()
    
    # Positive Findings
    pdf.add_page()
    pdf.section_title('Positive Findings', 1)
    pdf.body_text('The following security controls are implemented correctly:')
    pdf.ln(3)
    
    positives = [
        ('Mandatory 2FA Implementation', 
         'All users required to setup MFA before accessing system. TOTP-based with QR code enrollment.'),
        ('Group-Based Access Control', 
         'Users assigned to camera groups restrict viewable footage. Group ALL provides superuser access.'),
        ('Comprehensive Audit Logging Framework', 
         'All major actions logged (create, update, delete). IP address and user identification captured.'),
        ('Tamper Detection for Images', 
         'is_tampered flag with blur and entropy scoring. Separate viewing interface for tampered snapshots.'),
        ('Orphaned Snapshot Preservation', 
         'When cameras are deleted, snapshots preserved as orphaned. Prevents accidental loss.'),
        ('Storage Monitoring & Alerting', 
         'Automatic storage usage monitoring with growth rate prediction and configurable alerts.'),
    ]
    
    for title, desc in positives:
        pdf.set_font('Arial', 'B', 10)
        pdf.set_text_color(0, 128, 0)
        pdf.cell(0, 7, title, 0, 1, 'L')
        pdf.set_text_color(0, 0, 0)
        pdf.set_font('Arial', '', 10)
        pdf.multi_cell(0, 5, desc)
        pdf.ln(3)
    
    # Recommendations
    pdf.add_page()
    pdf.section_title('Recommendations (Prioritized)', 1)
    
    pdf.section_title('Immediate Actions (0-30 days)', 2)
    pdf.set_fill_color(0, 51, 102)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(25, 8, 'Priority', 1, 0, 'C', True)
    pdf.cell(120, 8, 'Action', 1, 0, 'C', True)
    pdf.cell(40, 8, 'Effort', 1, 1, 'C', True)
    
    immediate = [
        ('P0', 'Implement file hash (SHA-256) for all new snapshots/videos', 'Medium'),
        ('P0', 'Add deleted_at soft delete, disable permanent deletion', 'Low'),
        ('P1', 'Encrypt camera passwords with AES-256', 'Medium'),
        ('P1', 'Remove password from camera API response', 'Low'),
    ]
    
    for priority, action, effort in immediate:
        pdf.recommendation_row(priority, action, effort)
    
    pdf.ln(8)
    pdf.section_title('Short-term (1-3 months)', 2)
    pdf.set_fill_color(0, 51, 102)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(25, 8, 'Priority', 1, 0, 'C', True)
    pdf.cell(120, 8, 'Action', 1, 0, 'C', True)
    pdf.cell(40, 8, 'Effort', 1, 1, 'C', True)
    
    short_term = [
        ('P1', 'Implement append-only audit log with triggers', 'Medium'),
        ('P2', 'Add safety classification to cameras', 'Low'),
        ('P2', 'Implement retention hold for incident footage', 'Medium'),
        ('P2', 'Move snapshots outside web root, enforce API-only access', 'Medium'),
    ]
    
    for priority, action, effort in short_term:
        pdf.recommendation_row(priority, action, effort)
    
    # Conclusion
    pdf.add_page()
    pdf.section_title('Conclusion', 1)
    pdf.body_text('The B-SNAP system provides a functional foundation for CCTV management in mining '
                  'environments with good access controls and audit logging capabilities. However, '
                  'critical evidence protection gaps make it unsuitable for high-stakes mining operations '
                  'without immediate remediation.')
    
    pdf.ln(5)
    pdf.section_title('Key Risk', 2)
    pdf.set_font('Arial', 'B', 11)
    pdf.set_text_color(200, 0, 0)
    pdf.multi_cell(0, 6, 'The system cannot currently prove the integrity of CCTV footage, which is '
                          'essential for:')
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Arial', '', 10)
    pdf.cell(10, 6, '', 0, 0)
    pdf.cell(0, 6, '- Legal defense in accident litigation', 0, 1)
    pdf.cell(10, 6, '', 0, 0)
    pdf.cell(0, 6, '- Regulatory compliance demonstrations', 0, 1)
    pdf.cell(10, 6, '', 0, 0)
    pdf.cell(0, 6, '- Internal safety investigations', 0, 1)
    
    pdf.ln(10)
    pdf.set_fill_color(255, 240, 200)
    pdf.set_draw_color(200, 150, 0)
    pdf.set_line_width(0.5)
    pdf.rect(15, pdf.get_y(), 180, 35, 'DF')
    pdf.set_xy(20, pdf.get_y() + 3)
    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(150, 100, 0)
    pdf.cell(0, 8, 'RECOMMENDATION:', 0, 1)
    pdf.set_xy(20, pdf.get_y())
    pdf.set_font('Arial', '', 10)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(170, 6, 'Implement P0 and P1 recommendations immediately before using B-SNAP '
                              'for safety-critical mining operations where CCTV data serves as legal evidence.')
    
    # Save PDF
    output_path = 'B-SNAP_IT_Audit_Report.pdf'
    pdf.output(output_path)
    print(f'PDF generated successfully: {output_path}')
    print(f'Pages: {pdf.page_no()}')
    return output_path


if __name__ == '__main__':
    generate_pdf()
