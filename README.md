# KLEAN&KARE Delivery Tracking

ระบบตรวจสอบสถานะพัสดุ KLEAN&KARE สำหรับลูกค้าและทีม CS รองรับเลขงานที่ขึ้นต้นด้วย `26`,
เลขพัสดุ `ANB` ของ KEX/InterExpress และเลขคำสั่งซื้อ Shopee ที่นำเข้าไว้แล้ว

หน้า public แสดงเฉพาะเลขพัสดุ ผู้ให้บริการ สถานะ และเวลา ไม่แสดงชื่อลูกค้า ที่อยู่
ข้อมูลสินค้า พนักงานขนส่ง หรือข้อมูลภายในของบริษัท

## ความสามารถหลัก

- หน้า `/customer.html` สำหรับลูกค้าค้นหาโดยไม่ต้องเลือกขนส่ง
- ค้นหาเลข ANB ที่ KEX ก่อน แล้ว fallback ไป InterExpress
- แปลงเลขคำสั่งซื้อ Shopee เป็นเลขพัสดุจากข้อมูล Sell Report ที่นำเข้าแล้ว
- หน้า CS สำหรับตรวจสถานะสด สร้างรายงาน และเปิดหลักฐานหลัง login
- ตรวจ Skyfrog, KEX และ InterExpress
- นำเข้า Shopee Sell Report ด้วย Playwright/Chromium
- เก็บ mapping และ cache ใน SQLite
- เขียนสถานะกลับ Google Sheet ผ่าน Apps Script webhook
- ตั้งเวลาทำงานด้วย systemd timers

## โครงสร้างระบบ

```text
customer / CS browser
        |
   Nginx + HTTPS
        |
Gunicorn / Flask :8091
        |
        +-- Skyfrog API
        +-- KEX tracking
        +-- InterExpress API
        +-- SQLite: data/status-cache.sqlite
        +-- Shopee Sell Report importer
        +-- Google Sheet / Apps Script
```

ข้อมูล runtime ต่อไปนี้ไม่อยู่ใน GitHub และต้องสำรองแยกอย่างปลอดภัย:

- `.env`
- `data/status-cache.sqlite`
- `data/kex-proofs/`
- `outputs/`
- Shopee browser profile และไฟล์ Sell Report

## ความต้องการระบบ

- Python 3.10 ขึ้นไป
- Node.js 18 ขึ้นไป
- Chromium ที่ Playwright จัดการให้
- Linux พร้อม systemd สำหรับ production
- Nginx และ Certbot สำหรับ HTTPS

สำหรับ Hostinger ต้องใช้ **Hostinger VPS** ไม่ใช่ Shared Web Hosting เพราะระบบมี background jobs,
SQLite ที่เขียนข้อมูล, Gunicorn และ Chromium แบบ persistent profile โดย Hostinger มี
[Ubuntu 24.04 with Docker template](https://www.hostinger.com/support/8306612-how-to-use-the-docker-vps-template-at-hostinger/)
ให้เลือกได้ แต่ขั้นตอนด้านล่างใช้ Ubuntu + systemd เพื่อให้ย้ายงานทั้งหมดจาก Pi ได้ตรงกับระบบเดิม

## ติดตั้งในเครื่องสำหรับพัฒนา

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
npm ci
npx playwright install chromium
cp .env.example .env
```

กรอกค่าจริงใน `.env` แล้วรันเว็บ:

```bash
.venv/bin/gunicorn --workers 2 --threads 2 --timeout 90 \
  --bind 127.0.0.1:8091 klean_pod_checker.wsgi:app
```

ตรวจ health check ที่ `http://127.0.0.1:8091/health`

## Deploy บน Hostinger VPS

### 1. เตรียม VPS

เลือก Ubuntu 24.04 และตั้ง SSH key จากนั้นเชื่อมต่อด้วยบัญชี root:

```bash
ssh root@YOUR_VPS_IP
```

ติดตั้ง dependency และสร้าง service account ชื่อ `milk` ให้ตรงกับ systemd units ใน repository:

```bash
apt update
apt install -y git python3 python3-venv nodejs npm unzip \
  xvfb openbox x11vnc novnc websockify nginx certbot python3-certbot-nginx \
  ufw sqlite3 rsync
adduser --disabled-password --gecos "" milk
mkdir -p /opt/klean-pod-checker
chown milk:milk /opt/klean-pod-checker
```

เปิดเฉพาะ SSH, HTTP และ HTTPS:

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable
```

### 2. Clone private repository

สร้าง SSH key สำหรับ deploy และเพิ่ม public key เป็น Read-only Deploy Key ใน GitHub repository:

```bash
sudo -u milk ssh-keygen -t ed25519 -f /home/milk/.ssh/id_ed25519 -N ""
cat /home/milk/.ssh/id_ed25519.pub
sudo -u milk ssh-keyscan github.com >> /home/milk/.ssh/known_hosts
```

จากนั้น clone:

```bash
sudo -u milk git clone git@github.com:YOUR_GITHUB_ACCOUNT/klean-pod-checker.git \
  /opt/klean-pod-checker
cd /opt/klean-pod-checker
```

### 3. ติดตั้ง Python และ Playwright

```bash
sudo -u milk python3 -m venv .venv
sudo -u milk .venv/bin/pip install --upgrade pip
sudo -u milk .venv/bin/pip install -e .
sudo -u milk npm ci
sudo -u milk npx playwright install chromium
npx playwright install-deps chromium
```

### 4. ตั้งค่า secrets

```bash
sudo -u milk cp .env.example .env
sudo -u milk chmod 600 .env
sudo -u milk nano .env
```

ค่าที่ต้องกำหนดอย่างน้อย:

- `SHEET_ID`, `SHEET_GID`
- `SKYFROG_CUSTOMER_CODE`, `SKYFROG_USERNAME`, `SKYFROG_PASSWORD`
- `KEX_PROOF_PIN`
- `INTEREXPRESS_USERNAME`, `INTEREXPRESS_PASSWORD`
- `CS_ACCESS_PIN`
- `WEB_SECRET_KEY` สร้างด้วย `openssl rand -hex 32`
- `PUBLIC_BASE_URL`
- `GOOGLE_SHEETS_WEBHOOK_URL`, `GOOGLE_SHEETS_WEBHOOK_SECRET`

ห้ามส่ง `.env` ผ่าน GitHub, chat หรือ email

### 5. ย้ายข้อมูล runtime จากเครื่องเดิม

หยุดการเขียนข้อมูลบนเครื่องเดิมชั่วคราว แล้วส่งผ่าน SSH โดยตรง ไม่ผ่าน GitHub:

```bash
rsync -a --info=progress2 /opt/klean-pod-checker/data/ \
  milk@YOUR_VPS_IP:/opt/klean-pod-checker/data/
rsync -a --info=progress2 /home/milk/kleanandkare-shopee/ \
  milk@YOUR_VPS_IP:/home/milk/kleanandkare-shopee/
```

ตรวจ ownership บน Hostinger:

```bash
chown -R milk:milk /opt/klean-pod-checker /home/milk/kleanandkare-shopee
chmod 600 /opt/klean-pod-checker/.env
```

Shopee browser profile มี session login จึงต้องเก็บเหมือน credential และห้ามเปิด public

### 6. ติดตั้ง services และ timers

```bash
cp systemd/*.service systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now klean-pod-web.service
systemctl enable --now klean-pod-checker.timer
systemctl enable --now kleanandkare-shopee-browser.service
systemctl enable --now kleanandkare-shopee-vnc.service
systemctl enable --now kleanandkare-shopee-novnc.service
systemctl enable --now kleanandkare-shopee-report.timer
```

ตรวจสถานะ:

```bash
systemctl --no-pager --full status klean-pod-web.service
systemctl list-timers --all | grep -E 'klean|shopee'
curl -fsS http://127.0.0.1:8091/health
```

### 7. Login Shopee อย่างปลอดภัย

noVNC ฟังเฉพาะ `127.0.0.1:6081` บน VPS ให้เปิด SSH tunnel จากเครื่องผู้ดูแล:

```bash
ssh -L 6081:127.0.0.1:6081 root@YOUR_VPS_IP
```

เปิด `http://127.0.0.1:6081/vnc.html` แล้ว login Shopee ใน browser ที่เตรียมไว้
ห้ามเปิดพอร์ต 5901 หรือ 6081 ต่อสาธารณะ

### 8. ตั้ง Nginx และ HTTPS

ชี้ DNS `A record` ของโดเมนไปที่ VPS IP แล้วแก้ `server_name` ในไฟล์ตัวอย่าง:

```bash
sed 's/tracking.example.com/YOUR_DOMAIN/g' \
  deploy/nginx/klean-pod-checker.conf \
  > /etc/nginx/sites-available/klean-pod-checker
ln -s /etc/nginx/sites-available/klean-pod-checker \
  /etc/nginx/sites-enabled/klean-pod-checker
nginx -t
systemctl reload nginx
certbot --nginx -d YOUR_DOMAIN
```

Hostinger อธิบายทางเลือก SSL สำหรับ VPS ไว้ที่
[How to Install SSL on VPS](https://www.hostinger.com/support/6360129-how-to-install-ssl-on-vps-at-hostinger/)

## อัปเดตเวอร์ชันภายหลัง

```bash
cd /opt/klean-pod-checker
sudo -u milk git pull --ff-only
sudo -u milk .venv/bin/pip install -e .
sudo -u milk npm ci
sudo -u milk npx playwright install chromium
systemctl restart klean-pod-web.service
```

## คำสั่งงานประจำ

```bash
# ตรวจ 5 ออเดอร์
.venv/bin/klean-pod-checker --limit 5

# ตรวจเลขเดียว
.venv/bin/klean-pod-checker --order 260607E69813MF

# ใช้ไฟล์ CSV ในเครื่อง
.venv/bin/klean-pod-checker --sheet-csv orders.csv

# นำเข้า Shopee Sell Report ล่าสุด
.venv/bin/python -m klean_pod_checker.shopee_sales_sync \
  --database data/status-cache.sqlite
```

## ทดสอบ

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check klean_pod_checker/download_shopee_report.js
```

## Google Apps Script

โค้ดใน `google_apps_script/Code.gs` เขียนเฉพาะคอลัมน์สถานะที่ระบบดูแล ตั้ง
`WEBHOOK_SECRET` ใน Apps Script Properties ให้ตรงกับ `GOOGLE_SHEETS_WEBHOOK_SECRET`
และเก็บ deployment URL ไว้ใน `.env`

## Security checklist

- GitHub repository ต้องเป็น private
- ใช้ GitHub Deploy Key แบบ read-only บน VPS
- เปิด firewall เฉพาะ 22, 80 และ 443
- ใช้ SSH key และปิด password login หลังตรวจว่าสามารถเข้า VPS ได้
- สำรอง SQLite, proof images และ Shopee profile แบบเข้ารหัส
- ไม่แสดงชื่อลูกค้า ที่อยู่ หรือสินค้าในหน้า public
- หมุน credentials ใหม่ทันทีหาก `.env` หรือ Shopee profile หลุด
