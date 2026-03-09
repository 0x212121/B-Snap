"""Job Execution Log Model.

Track background job execution history for monitoring and debugging.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, BigInteger, Index
from app.db.database import Base


class JobExecutionLog(Base):
    """Log entry for background job execution.
    
    Tracks when jobs run, how long they take, and whether they succeed or fail.
    """
    
    __tablename__ = "job_execution_logs"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(String(100), nullable=False, index=True)
    job_name = Column(String(200), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, index=True)  # success, fail, timeout, running
    error_message = Column(Text, nullable=True)
    records_processed = Column(Integer, default=0)
    metadata_json = Column(Text, nullable=True)  # JSON string for additional data
    
    # Composite indexes for common queries
    __table_args__ = (
        Index('idx_job_status_time', 'job_id', 'status', 'started_at'),
        Index('idx_started_at_desc', started_at.desc()),
    )
    
    def __repr__(self):
        return f"<JobExecutionLog(id={self.id}, job={self.job_id}, status={self.status})>"
    
    @classmethod
    def start_execution(cls, db, job_id: str, job_name: str):
        """Create a new log entry when job starts.
        
        Args:
            db: Database session
            job_id: Unique job identifier
            job_name: Human-readable job name
            
        Returns:
            JobExecutionLog instance
        """
        log = cls(
            job_id=job_id,
            job_name=job_name,
            started_at=datetime.now(timezone.utc),
            status="running"
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log
    
    def complete(self, db, status: str, records: int = 0, error: str = None, metadata: dict = None):
        """Mark job execution as complete.
        
        Args:
            db: Database session
            status: final status (success, fail, timeout)
            records: number of records processed
            error: error message if failed
            metadata: additional data as dict
        """
        from json import dumps
        
        self.ended_at = datetime.now(timezone.utc)
        self.status = status
        self.records_processed = records
        
        if error:
            self.error_message = error[:2000]  # Limit error length
        
        if metadata:
            self.metadata_json = dumps(metadata)
        
        # Calculate duration
        if self.started_at:
            delta = self.ended_at - self.started_at
            self.duration_ms = int(delta.total_seconds() * 1000)
        
        db.commit()
    
    @classmethod
    def get_recent_executions(cls, db, job_id: str = None, limit: int = 50):
        """Get recent job executions.
        
        Args:
            db: Database session
            job_id: filter by specific job (optional)
            limit: max results
            
        Returns:
            List of JobExecutionLog instances
        """
        query = db.query(cls)
        
        if job_id:
            query = query.filter(cls.job_id == job_id)
        
        return query.order_by(cls.started_at.desc()).limit(limit).all()
    
    @classmethod
    def get_job_stats(cls, db, hours: int = 24):
        """Get job execution statistics.
        
        Args:
            db: Database session
            hours: time window in hours
            
        Returns:
            Dict with stats per job
        """
        from sqlalchemy import func, case
        from datetime import timedelta
        
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        results = db.query(
            cls.job_id,
            cls.job_name,
            func.count(cls.id).label('total_runs'),
            func.sum(case((cls.status == 'success', 1), else_=0)).label('success_count'),
            func.sum(case((cls.status == 'fail', 1), else_=0)).label('fail_count'),
            func.avg(cls.duration_ms).label('avg_duration_ms'),
            func.max(cls.started_at).label('last_run')
        ).filter(
            cls.started_at >= cutoff
        ).group_by(
            cls.job_id,
            cls.job_name
        ).all()
        
        def to_int(val):
            if val is None:
                return 0
            import decimal
            if isinstance(val, decimal.Decimal):
                return int(val)
            return int(val) if not isinstance(val, (int, float)) else int(val)
        
        def to_float(val):
            if val is None:
                return 0.0
            import decimal
            if isinstance(val, decimal.Decimal):
                return float(val)
            return float(val) if not isinstance(val, (int, float)) else float(val)
        
        return {
            row.job_id: {
                'job_name': row.job_name,
                'total_runs': to_int(row.total_runs),
                'success_count': to_int(row.success_count),
                'fail_count': to_int(row.fail_count),
                'success_rate': float(round(to_float(row.success_count) / to_float(row.total_runs) * 100, 1)) if to_float(row.total_runs) > 0 else 0.0,
                'avg_duration_ms': to_int(row.avg_duration_ms),
                'last_run': row.last_run.isoformat() if row.last_run else None
            }
            for row in results
        }
    
    @classmethod
    def cleanup_old_logs(cls, db, days: int = 30):
        """Delete old execution logs.
        
        Args:
            db: Database session
            days: retention period in days
        """
        from datetime import timedelta
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = db.query(cls).filter(cls.started_at < cutoff).delete(synchronize_session=False)
        db.commit()
        return deleted
