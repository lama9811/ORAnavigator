# CS Navigator Deployment Guide

> **Complete step-by-step tutorial for deploying CS Navigator on a new AWS account**
>
> Author: Aayush Shrestha
> Last Updated: January 2026
> Project: CS Navigator - AI Chatbot for Morgan State University

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Part 1: AWS Setup](#part-1-aws-setup)
   - [1.1 Create EC2 Instance](#11-create-ec2-instance)
   - [1.2 Create RDS MySQL Database](#12-create-rds-mysql-database)
   - [1.3 Configure Security Groups](#13-configure-security-groups)
5. [Part 2: Configuration Files](#part-2-configuration-files)
   - [2.1 Files That Need Changes](#21-files-that-need-changes)
   - [2.2 Update .env File](#22-update-env-file)
   - [2.3 Update deploy.sh](#23-update-deploysh)
   - [2.4 Update docker-compose.yml](#24-update-docker-composeyml)
   - [2.5 Add New PEM Key](#25-add-new-pem-key)
6. [Part 3: EC2 Server Setup](#part-3-ec2-server-setup)
7. [Part 4: Deployment](#part-4-deployment)
8. [Part 5: Database Migration](#part-5-database-migration)
9. [Verification & Testing](#verification--testing)
10. [Troubleshooting](#troubleshooting)
11. [Quick Reference](#quick-reference)
12. [File Structure](#file-structure)

---

## Overview

CS Navigator is a full-stack AI chatbot application that helps Morgan State University CS students with academic advising, course information, and DegreeWorks integration.

### Tech Stack

| Component | Technology |
|-----------|------------|
| Frontend | React 19 + Vite + Tailwind CSS |
| Backend | Python FastAPI + Uvicorn |
| Database | AWS RDS MySQL |
| Vector DB | Pinecone (cloud-hosted) |
| AI/LLM | OpenAI GPT-3.5 |
| Containerization | Docker + Docker Compose |
| Hosting | AWS EC2 |
| Web Server | Nginx (frontend) |

### What This Guide Covers

This guide walks you through deploying CS Navigator on a **new AWS account**, including:
- Creating new AWS infrastructure (EC2, RDS)
- Updating configuration files
- Setting up the server
- Deploying the application
- Migrating existing data (optional)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           USER'S BROWSER                                │
│                     http://EC2_PUBLIC_IP:3000                           │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         AWS EC2 INSTANCE                                │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                    Docker Network: app-network                     │ │
│  │                                                                    │ │
│  │  ┌─────────────────────────┐    ┌─────────────────────────────┐   │ │
│  │  │   FRONTEND CONTAINER    │    │    BACKEND CONTAINER        │   │ │
│  │  │   (Nginx + React)       │    │    (FastAPI + Uvicorn)      │   │ │
│  │  │                         │    │                             │   │ │
│  │  │   Port: 3000:80         │───▶│    Port: 5000:5000          │   │ │
│  │  │                         │    │                             │   │ │
│  │  │   /api/* ──────────────────▶ │    Handles API requests     │   │ │
│  │  │   /uploads/* ──────────────▶ │    File uploads             │   │ │
│  │  │                         │    │                             │   │ │
│  │  └─────────────────────────┘    └──────────────┬──────────────┘   │ │
│  │                                                │                   │ │
│  └────────────────────────────────────────────────┼───────────────────┘ │
└───────────────────────────────────────────────────┼─────────────────────┘
                                                    │
                    ┌───────────────────────────────┼───────────────────┐
                    │                               │                   │
                    ▼                               ▼                   ▼
        ┌───────────────────┐         ┌─────────────────┐    ┌─────────────────┐
        │   AWS RDS MySQL   │         │    Pinecone     │    │     OpenAI      │
        │                   │         │   Vector DB     │    │    GPT-3.5      │
        │  - Users          │         │                 │    │                 │
        │  - DegreeWorks    │         │  - Embeddings   │    │  - Chat         │
        │  - Tickets        │         │  - RAG Search   │    │  - Responses    │
        └───────────────────┘         └─────────────────┘    └─────────────────┘
```

### Deployment Flow

```
Local Machine                    Docker Hub                     EC2 Instance
     │                               │                               │
     │  1. Build Docker Images       │                               │
     │──────────────────────────────▶│                               │
     │                               │                               │
     │  2. Push to Docker Hub        │                               │
     │──────────────────────────────▶│  sakina593/chatbot-frontend   │
     │                               │  sakina593/chatbot-backend    │
     │                               │                               │
     │  3. SSH to EC2                │                               │
     │───────────────────────────────────────────────────────────────▶│
     │                               │                               │
     │                               │  4. Pull Images               │
     │                               │◀──────────────────────────────│
     │                               │                               │
     │                               │                               │
     │  5. Docker Compose Up         │                               │
     │───────────────────────────────────────────────────────────────▶│
     │                               │                               │
     │                               │                    ┌──────────┴──────────┐
     │                               │                    │  Frontend :3000     │
     │                               │                    │  Backend  :5000     │
     │                               │                    └─────────────────────┘
```

---

## Prerequisites

### On Your Local Machine

- [ ] Git installed
- [ ] Docker Desktop installed and running
- [ ] Docker Hub account (username: `sakina593`)
- [ ] SSH client (built into Windows 10+, Mac, Linux)
- [ ] Text editor (VS Code recommended)

### AWS Account Requirements

- [ ] AWS account with admin access
- [ ] Ability to create EC2 instances
- [ ] Ability to create RDS databases
- [ ] Understanding of security groups

### Credentials You'll Need

| Credential | Where to Get It | Used For |
|------------|-----------------|----------|
| Docker Hub password | hub.docker.com | Pushing/pulling images |
| AWS Console access | aws.amazon.com | Creating infrastructure |
| OpenAI API key | Already in .env | AI responses (keep same) |
| Pinecone API key | Already in .env | Vector search (keep same) |

---

## Part 1: AWS Setup

### 1.1 Create EC2 Instance

1. **Log into AWS Console** → Go to EC2 Dashboard

2. **Click "Launch Instance"**

3. **Configure the instance:**

   | Setting | Value |
   |---------|-------|
   | Name | `cs-navigator-server` |
   | AMI | Amazon Linux 2023 AMI (Free tier eligible) |
   | Instance type | `t2.micro` (Free tier) or `t2.small` (recommended) |
   | Key pair | Create new → Download `.pem` file |
   | Network | Default VPC |
   | Auto-assign public IP | Enable |
   | Storage | 20 GB gp3 |

4. **Create or select Security Group** (see section 1.3)

5. **Launch the instance**

6. **Note down:**
   - Public IPv4 address (e.g., `54.xxx.xxx.xxx`)
   - Instance ID
   - Key pair name and location of `.pem` file

### 1.2 Create RDS MySQL Database

1. **Go to RDS Dashboard** → Click "Create database"

2. **Configure the database:**

   | Setting | Value |
   |---------|-------|
   | Engine | MySQL |
   | Version | 8.0.x (latest) |
   | Template | Free tier |
   | DB instance identifier | `cs-navigator-db` |
   | Master username | `admin` (or your choice) |
   | Master password | Create a strong password |
   | Instance class | `db.t3.micro` (Free tier) |
   | Storage | 20 GB gp2 |
   | Public access | Yes (for initial setup) |
   | VPC | Same as EC2 |

3. **After creation, note down:**
   - Endpoint (e.g., `cs-navigator-db.xxxxx.us-east-1.rds.amazonaws.com`)
   - Port: `3306`
   - Master username
   - Master password

4. **Create the database schema:**

   Connect to RDS and create the database:
   ```bash
   mysql -h YOUR_RDS_ENDPOINT -u admin -p
   ```

   Then run:
   ```sql
   CREATE DATABASE chatbot;
   CREATE USER 'chatuser'@'%' IDENTIFIED BY 'YourStrongPassword123!';
   GRANT ALL PRIVILEGES ON chatbot.* TO 'chatuser'@'%';
   FLUSH PRIVILEGES;
   EXIT;
   ```

### 1.3 Configure Security Groups

You need TWO security groups:

#### EC2 Security Group

| Type | Port | Source | Description |
|------|------|--------|-------------|
| SSH | 22 | Your IP | SSH access |
| Custom TCP | 3000 | 0.0.0.0/0 | Frontend |
| Custom TCP | 5000 | 0.0.0.0/0 | Backend API |
| HTTP | 80 | 0.0.0.0/0 | Web traffic |
| HTTPS | 443 | 0.0.0.0/0 | Secure web traffic |

#### RDS Security Group

| Type | Port | Source | Description |
|------|------|--------|-------------|
| MySQL/Aurora | 3306 | EC2 Security Group | Allow EC2 to connect |
| MySQL/Aurora | 3306 | Your IP | Allow local MySQL client |

---

## Part 2: Configuration Files

### 2.1 Files That Need Changes

Only **4 files** need to be modified:

| File | Location | What to Change |
|------|----------|----------------|
| `.env` | Project root | Database URL, EC2 IP |
| `deploy.sh` | Project root | EC2 IP, PEM key name |
| `docker-compose.yml` | Project root | EC2 IP in build args |
| `*.pem` | Project root | Add new key file |

### 2.2 Update .env File

**Location:** `cs-chatbot-morganstate/.env`

#### Current Values (OLD):
```ini
DATABASE_URL=mysql+pymysql://chatuser:FinalTest2025@chatbot-db.cin2gkcg8wp3.us-east-1.rds.amazonaws.com:3306/chatbot

VITE_API_BASE_URL=http://18.214.136.155:5000
VITE_API_URL=http://18.214.136.155:5000
```

#### New Values (UPDATE THESE):
```ini
# ============================================
# DATABASE - UPDATE WITH YOUR NEW RDS DETAILS
# ============================================
DATABASE_URL=mysql+pymysql://YOUR_DB_USER:YOUR_DB_PASSWORD@YOUR_RDS_ENDPOINT:3306/chatbot

# Example:
# DATABASE_URL=mysql+pymysql://chatuser:NewPassword123!@cs-navigator-db.abc123.us-east-1.rds.amazonaws.com:3306/chatbot

# ============================================
# API URLs - UPDATE WITH YOUR NEW EC2 IP
# ============================================
VITE_API_BASE_URL=http://YOUR_EC2_IP:5000
VITE_API_URL=http://YOUR_EC2_IP:5000

# Example:
# VITE_API_BASE_URL=http://54.123.45.67:5000
# VITE_API_URL=http://54.123.45.67:5000
```

#### Values That Stay The Same:
```ini
# These can remain unchanged - they work across AWS accounts
OPENAI_API_KEY=sk-proj-... (keep existing)
PINECONE_API_KEY=pcsk_... (keep existing)
PINECONE_INDEX_NAME=vectorized-datasource
PINECONE_CLOUD=aws
PINECONE_ENV=us-east-1
PINECONE_NAMESPACE=docs
PINECONE_HOST=https://vectorized-datasource-ivvl76a.svc.aped-4627-b74a.pinecone.io

DB_HOST=db
DB_PORT=3306
DB_USER=chatuser
DB_PASSWORD=StrongPass!
DB_NAME=chatbot

JWT_SECRET=... (keep existing or generate new)
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=240
```

### 2.3 Update deploy.sh

**Location:** `cs-chatbot-morganstate/deploy.sh`

Find these lines near the top:

#### Current Values (OLD):
```bash
EC2_HOST="18.214.136.155"
EC2_USER="ec2-user"
EC2_KEY="cs-chatbot-key.pem"
```

#### New Values (UPDATE THESE):
```bash
# ============================================
# UPDATE THESE WITH YOUR NEW AWS DETAILS
# ============================================
EC2_HOST="YOUR_NEW_EC2_IP"        # e.g., "54.123.45.67"
EC2_USER="ec2-user"                # Usually stays the same for Amazon Linux
EC2_KEY="your-new-key.pem"         # Name of your new PEM file
```

Also update the API_URL variable:
```bash
API_URL="http://YOUR_NEW_EC2_IP:5000"
```

### 2.4 Update docker-compose.yml

**Location:** `cs-chatbot-morganstate/docker-compose.yml`

Find this section in the frontend service:

#### Current Values (OLD):
```yaml
frontend:
  build:
    context: ./frontend
    dockerfile: Dockerfile
    args:
      - VITE_API_BASE_URL=http://18.214.136.155:5000
```

#### New Values (UPDATE THIS):
```yaml
frontend:
  build:
    context: ./frontend
    dockerfile: Dockerfile
    args:
      - VITE_API_BASE_URL=http://YOUR_NEW_EC2_IP:5000
```

### 2.5 Add New PEM Key

1. **Copy your new `.pem` file** to the project root:
   ```
   cs-chatbot-morganstate/
   ├── your-new-key.pem    ← Put it here
   ├── deploy.sh
   ├── docker-compose.yml
   └── ...
   ```

2. **Set correct permissions** (required for SSH):

   On Mac/Linux:
   ```bash
   chmod 400 your-new-key.pem
   ```

   On Windows (PowerShell as Admin):
   ```powershell
   icacls "your-new-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
   ```

---

## Part 3: EC2 Server Setup

### Connect to Your EC2 Instance

```bash
ssh -i "your-new-key.pem" ec2-user@YOUR_EC2_IP
```

### Install Docker and Docker Compose

Run these commands on your EC2 instance:

```bash
# Update system packages
sudo yum update -y

# Install Docker
sudo yum install docker -y

# Start Docker service
sudo service docker start

# Add ec2-user to docker group (so you don't need sudo)
sudo usermod -a -G docker ec2-user

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose

# Make Docker Compose executable
sudo chmod +x /usr/local/bin/docker-compose

# Enable Docker to start on boot
sudo systemctl enable docker

# IMPORTANT: Log out and back in for group changes to take effect
exit
```

### Verify Installation

SSH back in and verify:

```bash
ssh -i "your-new-key.pem" ec2-user@YOUR_EC2_IP

# Check Docker
docker --version
# Expected: Docker version 20.x.x or higher

# Check Docker Compose
docker-compose --version
# Expected: Docker Compose version 2.x.x

# Test Docker works without sudo
docker run hello-world
```

---

## Part 4: Deployment

### On Your Local Machine

#### Step 1: Clone or Navigate to the Project

```bash
# If cloning fresh:
git clone https://github.com/theaayushstha1/cs-chatbot-morganstate.git
cd cs-chatbot-morganstate

# Or if you already have it:
cd /path/to/cs-chatbot-morganstate
```

#### Step 2: Verify All Configuration Changes

Before deploying, double-check these files are updated:

```bash
# Check .env has new RDS endpoint and EC2 IP
cat .env | grep -E "(DATABASE_URL|VITE_API)"

# Check deploy.sh has new EC2 IP and key
cat deploy.sh | grep -E "(EC2_HOST|EC2_KEY)"

# Check docker-compose.yml has new EC2 IP
cat docker-compose.yml | grep "VITE_API_BASE_URL"

# Check PEM file exists
ls -la *.pem
```

#### Step 3: Login to Docker Hub

```bash
docker login
# Username: sakina593
# Password: (enter your Docker Hub password)
```

#### Step 4: Run the Deployment Script

```bash
# Make sure the script is executable
chmod +x deploy.sh

# Run deployment
bash deploy.sh
```

### What the Deploy Script Does

1. **Builds Docker images** locally (with `linux/amd64` platform)
2. **Pushes images** to Docker Hub
3. **SSHs to EC2** and copies docker-compose.yml + .env
4. **Pulls the new images** on EC2
5. **Restarts containers** with new images

### Expected Output

```
[1/4] Building Docker images...
 => Building frontend...
 => Building backend...
[2/4] Pushing to Docker Hub...
 => sakina593/chatbot-frontend:latest pushed
 => sakina593/chatbot-backend:latest pushed
[3/4] Connecting to EC2...
 => SSH connection successful
 => Copying files...
[4/4] Starting containers...
 => Pulling latest images...
 => Starting services...
 => Cleaning up old images...

Deployment complete!
Frontend: http://YOUR_EC2_IP:3000
Backend:  http://YOUR_EC2_IP:5000
```

---

## Part 5: Database Migration

If you need to migrate data from the old database to the new one:

### Option A: Fresh Start (No Migration)

The application will automatically create tables when it first connects. Just deploy and register new users.

### Option B: Migrate Existing Data

#### Export from Old Database

```bash
# On your local machine, export the old database
mysqldump -h OLD_RDS_ENDPOINT -u chatuser -p chatbot > chatbot_backup.sql
```

#### Import to New Database

```bash
# Import to new database
mysql -h NEW_RDS_ENDPOINT -u chatuser -p chatbot < chatbot_backup.sql
```

### Verify Database Connection

SSH into EC2 and check the backend logs:

```bash
ssh -i "your-new-key.pem" ec2-user@YOUR_EC2_IP

# View backend logs
docker logs ec2-user-backend-1

# Look for:
# "INFO: Application startup complete"
# "INFO: Connected to database"
```

---

## Verification & Testing

### Check Services Are Running

```bash
# SSH into EC2
ssh -i "your-new-key.pem" ec2-user@YOUR_EC2_IP

# Check running containers
docker ps

# Expected output:
# CONTAINER ID   IMAGE                            STATUS          PORTS
# abc123...      sakina593/chatbot-frontend       Up X minutes    0.0.0.0:3000->80/tcp
# def456...      sakina593/chatbot-backend        Up X minutes    0.0.0.0:5000->5000/tcp
```

### Test Endpoints

From your browser or using curl:

```bash
# Test frontend (should return HTML)
curl http://YOUR_EC2_IP:3000

# Test backend health
curl http://YOUR_EC2_IP:5000/api/health

# Test backend docs (Swagger UI)
# Open in browser: http://YOUR_EC2_IP:5000/docs
```

### Test the Application

1. Open `http://YOUR_EC2_IP:3000` in your browser
2. Try registering a new user
3. Try logging in
4. Try asking the chatbot a question
5. Check that responses are generated

---

## Troubleshooting

### Common Issues and Solutions

#### Issue: "Permission denied" when running deploy.sh

**Solution:**
```bash
chmod +x deploy.sh
chmod 400 your-new-key.pem
```

#### Issue: "Connection refused" on port 3000 or 5000

**Solution:** Check security group allows these ports
```bash
# On EC2, check if containers are running
docker ps

# Check if ports are listening
sudo netstat -tlnp | grep -E "(3000|5000)"
```

#### Issue: "Database connection failed"

**Solution:**
1. Verify RDS security group allows EC2
2. Check DATABASE_URL in .env is correct
3. Verify RDS is running in AWS console

```bash
# Test database connection from EC2
docker exec -it ec2-user-backend-1 python -c "from db import engine; print(engine.connect())"
```

#### Issue: "Docker login failed"

**Solution:**
```bash
# Try logging in again
docker logout
docker login
```

#### Issue: "Image pull failed"

**Solution:**
```bash
# Make sure you pushed the images
docker images | grep sakina593

# Try pushing again
docker push sakina593/chatbot-frontend:latest
docker push sakina593/chatbot-backend:latest
```

#### Issue: "502 Bad Gateway" or blank page

**Solution:** Backend might not be running
```bash
# Check backend logs
docker logs ec2-user-backend-1

# Restart containers
docker-compose down
docker-compose up -d
```

### View Logs

```bash
# View all logs
docker-compose logs

# View specific service logs
docker logs ec2-user-backend-1
docker logs ec2-user-frontend-1

# Follow logs in real-time
docker logs -f ec2-user-backend-1
```

### Restart Services

```bash
# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart backend

# Full restart (stop and start)
docker-compose down
docker-compose up -d
```

---

## Quick Reference

### Configuration Checklist

| Item | File | What to Update |
|------|------|----------------|
| Database URL | `.env` | `DATABASE_URL=mysql+pymysql://user:pass@endpoint:3306/chatbot` |
| API URL 1 | `.env` | `VITE_API_BASE_URL=http://EC2_IP:5000` |
| API URL 2 | `.env` | `VITE_API_URL=http://EC2_IP:5000` |
| EC2 Host | `deploy.sh` | `EC2_HOST="EC2_IP"` |
| PEM Key | `deploy.sh` | `EC2_KEY="your-key.pem"` |
| Frontend Build | `docker-compose.yml` | `VITE_API_BASE_URL=http://EC2_IP:5000` |

### Useful Commands

```bash
# === LOCAL MACHINE ===
# Deploy
bash deploy.sh

# Quick deploy (no rebuild)
bash quickdeploy.sh

# View remote logs
bash viewlogs.sh

# === ON EC2 ===
# Check status
docker ps

# View logs
docker-compose logs

# Restart
docker-compose restart

# Full restart
docker-compose down && docker-compose up -d

# Check disk space
df -h

# Check memory
free -m
```

### Important URLs

| Service | URL |
|---------|-----|
| Frontend | `http://YOUR_EC2_IP:3000` |
| Backend API | `http://YOUR_EC2_IP:5000` |
| API Docs (Swagger) | `http://YOUR_EC2_IP:5000/docs` |
| Health Check | `http://YOUR_EC2_IP:5000/api/health` |

### AWS Resources Created

| Resource | Name | Notes |
|----------|------|-------|
| EC2 Instance | cs-navigator-server | Runs Docker containers |
| RDS Database | cs-navigator-db | MySQL 8.0 |
| Key Pair | your-key-name | SSH access |
| Security Group (EC2) | - | Ports 22, 80, 443, 3000, 5000 |
| Security Group (RDS) | - | Port 3306 |

---

## File Structure

```
cs-chatbot-morganstate/
├── .env                          # Environment variables (UPDATE THIS)
├── deploy.sh                     # Main deployment script (UPDATE THIS)
├── quickdeploy.sh               # Quick redeploy (pulls only)
├── viewlogs.sh                  # Remote log viewer
├── docker-compose.yml           # Container orchestration (UPDATE THIS)
├── your-new-key.pem             # SSH key (ADD THIS)
├── DEPLOYMENT_GUIDE.md          # This guide
│
├── frontend/
│   ├── Dockerfile               # Frontend container build
│   ├── nginx.conf               # Nginx routing config
│   ├── package.json             # Node dependencies
│   ├── vite.config.js           # Vite configuration
│   └── src/                     # React source code
│
├── backend/
│   ├── Dockerfile               # Backend container build
│   ├── requirements.txt         # Python dependencies
│   ├── main.py                  # FastAPI application
│   ├── db.py                    # Database connection
│   ├── models.py                # SQLAlchemy models
│   └── security.py              # Authentication logic
│
└── README.md                    # Project readme
```

---

## Support

If you encounter issues:

1. Check the [Troubleshooting](#troubleshooting) section above
2. View logs using `bash viewlogs.sh` or `docker logs`
3. Verify all configuration values are correct
4. Ensure AWS security groups allow required ports

---

**Happy Deploying!**
