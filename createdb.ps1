# ==============================

# CONFIG

# ==============================

$CONTAINER_NAME = "bsnap-postgres"
$DB_NAME = "bsnap_db"
$DB_USER = "bsnap_user"
$DB_PASS = "bsnap_pass"
$BACKUP_FILE = "backup.sql"

# ==============================

# CLEANUP (opsional, hapus lama)

# ==============================

docker rm -f $CONTAINER_NAME 2>$null
docker volume rm postgres_data 2>$null

# ==============================

# RUN CONTAINER

# ==============================

docker run -d   --name $CONTAINER_NAME -e POSTGRES_DB=$DB_NAME   -e POSTGRES_USER=$DB_USER -e POSTGRES_PASSWORD=$DB_PASS   -p 5432:5432 -v b-snap2_postgres_data:/var/lib/postgresql/data   --restart unless-stopped postgres:17.5

# ==============================

# WAIT DATABASE READY

# ==============================

Write-Host "Menunggu PostgreSQL siap..."

Start-Sleep -Seconds 5

for ($i=0; $i -lt 10; $i++) {
$result = docker exec $CONTAINER_NAME pg_isready -U $DB_USER -d $DB_NAME 2>$null
if ($result -match "accepting connections") {
Write-Host "PostgreSQL siap!"
break
}
Start-Sleep -Seconds 2
}

# ==============================

# COPY FILE KE CONTAINER

# ==============================

docker cp $BACKUP_FILE ${CONTAINER_NAME}:/backup.sql

# ==============================

# IMPORT DATABASE

# ==============================

Write-Host "Import database..."

docker exec -i $CONTAINER_NAME psql -U $DB_USER -d $DB_NAME -f /backup.sql

# ==============================

# CEK TABEL

# ==============================

Write-Host "List tabel:"

docker exec -it $CONTAINER_NAME psql -U $DB_USER -d $DB_NAME -c "\dt"
