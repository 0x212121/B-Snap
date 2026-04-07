# Development Guide

## Prerequisites
- **Python** ≥ 3.13  
- **PostgreSQL** ≥ 17.5  
- **n8n** (required for WhatsApp integration)  
- **Node.js + npm** (for Tailwind CSS build)  

## Setup

```bash
# masuk ke project
cd b-snap
```

## Buat virtual environment
```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
```
## install dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Frontend (Tailwind CSS)
```bash

# install dependencies (once)
npm install -D tailwindcss@3.4.1 postcss autoprefixer

# run watcher (rebuild CSS on change)
npm run dev
```

## Tips

Simpan .env untuk variabel seperti DB connection, API key, dsb.

Jangan commit .env → tambahkan ke .gitignore.

Gunakan pre-commit hooks (optional) untuk linting/formatting sebelum push.
"""
