# Deployment

## Development
```bash
uvicorn app.main:app --reload
```


## Production
Deploy using docker compose

docker-compose.yml file:
```docker
services:
  b-snap:
    image: wijayaindra21/b-snap:v1.10.0
    container_name: bsnap-app
    ports:
      - "8080:8080"
    cap_add:
      - NET_RAW
    env_file:
      - .env
    depends_on:
      - postgres
    volumes:
      - ./static/snapshots:/static/snapshots
      - ./static/videos:/static/videos
      - ./logs:/logs
      - shared_tmp:/tmp/shared
    restart: unless-stopped

  scheduler:
    image: wijayaindra21/b-snap:v1.10.0
    container_name: bsnap-scheduler
    env_file:
      - .env
    working_dir: /
    depends_on:
      - b-snap
    volumes:
      - ./static/snapshots:/static/snapshots
      - ./logs:/logs
      - shared_tmp:/tmp/shared

    restart: unless-stopped
    command: python -m app.jobs.scheduler_main
  
  notifier:
    image: wijayaindra21/b-snap:v1.10.0
    container_name: bsnap-notifier
    env_file:
      - .env
    depends_on:
      - postgres
    restart: unless-stopped
    command: python -m ws.notifier

  postgres:
    image: postgres:17.5
    container_name: bsnap-postgres
    environment:
      POSTGRES_DB: bsnap_db
      POSTGRES_USER: bsnap_user
      POSTGRES_PASSWORD: bsnap_pass
      POSTGRES_HOST_AUTH_METHOD: scram-sha-256
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  pgadmin:
    image: dpage/pgadmin4
    container_name: bsnap-pgadmin
    environment:
      PGADMIN_DEFAULT_EMAIL: wijaya.indra2196@gmail.com
      PGADMIN_DEFAULT_PASSWORD: admin123
    ports:
      - "5050:80"
    depends_on:
      - postgres
    restart: unless-stopped

volumes:
  postgres_data:
  shared_tmp:
```

Set .env file like below:
```bash
DATABASE_URL=postgresql+psycopg2://bsnap_user:bsnap_pass@postgres:5432/bsnap_db
SECRET_KEY = "YourSecretKey"
TZ=Asia/Singapore
WORKERS=2
SMTP_HOST=192.168.0.1 # Your SMTP Server
SMTP_PORT=587
SMTP_USER=YourSMTPUser
SMTP_PASS=SMTPUserPassword
EMAIL_FROM=bsnap-noreply@example.com
```

Other valid environment variables:
```bash
MAX_WEB_SESSIONS=1  # Maximum concurrent logins per user (default 1)
APP_DEBUG=1 # DEBUG mode
```
Adjust WORKERS variable as needed: eg. 4 cpu -> 5 worker

Run:
```bash
docker compose up -d
```