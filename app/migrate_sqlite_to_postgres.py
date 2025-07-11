from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models_sql import *
from app.db.database import Base


# --- Koneksi ke SQLite lama ---
sqlite_engine = create_engine("sqlite:///app/data.db")
SQLiteSession = sessionmaker(bind=sqlite_engine)
sqlite_db = SQLiteSession()

# --- Koneksi ke PostgreSQL baru ---
postgres_engine = create_engine("postgresql+psycopg2://postgres:nokia311@localhost:5432/bsnap_db")
Base.metadata.create_all(bind=postgres_engine)
PostgresSession = sessionmaker(bind=postgres_engine)
postgres_db = PostgresSession()

# --- Fungsi untuk migrasi model dengan sanitasi data dan rollback per-record jika error ---
def migrate_table(model, delete_first=True):
    try:
        # if delete_first:
        #     postgres_db.query(model).delete()
        #     postgres_db.commit()
        print(f"\n🚀 Migrating {model.__tablename__}...")

        records = sqlite_db.query(model).all()
        success_count = 0
        error_count = 0

        for r in records:
            data = r.__dict__.copy()
            data.pop("_sa_instance_state", None)

            # Convert empty string to None
            for k, v in data.items():
                if v == "":
                    data[k] = None

            try:
                postgres_db.add(model(**data))
                postgres_db.flush()  # Force insert (will raise if constraint error)
                success_count += 1
            except Exception as row_err:
                postgres_db.rollback()
                print(f"⚠️ Skipped row in {model.__tablename__} due to error: {row_err}")
                error_count += 1

        postgres_db.commit()
        print(f"✅ {success_count} records migrated from {model.__tablename__}")
        if error_count:
            print(f"❌ {error_count} records failed (see logs above)")
    except Exception as e:
        postgres_db.rollback()
        print(f"❌ Error migrating {model.__tablename__}: {e}")

# --- Jalankan migrasi ---
if __name__ == "__main__":
    migrate_table(Camera, delete_first=True)
    migrate_table(NVR, delete_first=True)
    migrate_table(User, delete_first=True)
    migrate_table(CameraHealth, delete_first=True)
    migrate_table(CameraDailyStats, delete_first=True)
    migrate_table(CameraStatusChangeLog, delete_first=True)
    migrate_table(Snapshot, delete_first=True)
    migrate_table(Video, delete_first=True)
    migrate_table(SnapshotLog, delete_first=True)
    migrate_table(AuditLog, delete_first=True)
    migrate_table(HealthCheckStatus, delete_first=True)
    migrate_table(Configuration, delete_first=True)


    print("\n🎉 Migrasi selesai!")
    sqlite_db.close()
    postgres_db.close()
