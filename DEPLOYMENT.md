# Traffic Impact Assessment (TIA) - Deployment & Setup Guide

## Quick Start

### Prerequisites
- Python 3.10 or higher
- Git (for version control and hooks)
- Modern web browser (Chrome, Firefox, Safari, Edge)

### Installation

#### 1. Clone or obtain the repository
```
git clone https://github.com/crsanju/Cromton-Traffic-Impact-Asssessment.git
cd Cromton-Traffic-Impact-Asssessment
```

#### 2. Set up Python environment
```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment (Windows)
.\.venv\Scripts\Activate.ps1

# Activate virtual environment (macOS/Linux)
source .venv/bin/activate
```

#### 3. Install dependencies
```powershell
pip install -r requirements.txt
```

#### 4. Install Git hooks (optional but recommended)
```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
```

This ensures that when you stage `index.html`, `index_formulas.html` is automatically synced and staged.

---

## Running the Application

### Start the Python Report Service
```powershell
# Default: runs on http://127.0.0.1:8000
python report_service.py

# Or with custom configuration:
$env:HOST="0.0.0.0"
$env:PORT="8000"
python report_service.py
```

The service will be available at:
- **Main UI**: http://127.0.0.1:8000/docs (Swagger UI)
- **Health Check**: http://127.0.0.1:8000/health
- **Report Editor**: http://127.0.0.1:8000/report/editor/{draft_id}

### Open the Frontend
1. In your web browser, open the index.html file directly
2. Or run a local web server:
   ```powershell
   python -m http.server 8080
   ```
   Then visit: http://127.0.0.1:8080/index.html

---

## File Structure

### Key Files

| File | Purpose |
|------|---------|
| `index.html` | Main production user interface |
| `index_formulas.html` | Formula-detailed view (synced from index.html) |
| `index_developer.html` | Developer/beta editing file (isolated) |
| `report_service.py` | FastAPI backend for report generation |
| `manual.html` | User manual and documentation |
| `tia-shared-sync.js` | Shared formula synchronization library |
| `*.geojson` | Geographic data for traffic zones |

### Subdirectories

- `.git/` - Git repository metadata
- `.githooks/` - Custom Git hooks (pre-commit sync)
- `.venv/` - Python virtual environment (created during setup)
- `scripts/` - Utility PowerShell scripts

---

## Development Workflow

### Standard Git Workflow

1. **Edit the main file:**
   ```powershell
   # Make changes to index.html
   ```

2. **Stage and commit:**
   ```powershell
   git add index.html
   # Git hook automatically syncs and stages index_formulas.html
   git commit -m "Update TIA calculations"
   ```

### For Beta/Developer Changes

Use `index_developer.html` for isolated development:
```powershell
git add index_developer.html
git commit -m "Testing new feature in developer build"
```

To promote changes back to main:
```powershell
# Copy tested code from index_developer.html to index.html
# Then sync formulas
powershell -ExecutionPolicy Bypass -File scripts/sync-index-formulas.ps1
```

### Manual Syncing

If hooks aren't working:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/sync-index-formulas.ps1 -Quiet
```

---

## Configuration

### Environment Variables (Python Service)

```
HOST         - Server hostname (default: 127.0.0.1)
PORT         - Server port (default: 8000)
RELOAD       - Auto-reload on code changes (set to "true" for development)
```

Example:
```powershell
$env:HOST="0.0.0.0"
$env:PORT="5000"
$env:RELOAD="true"
python report_service.py
```

---

## Features

### Frontend Capabilities
- ✅ Interactive traffic analysis calculations
- ✅ Real-time map visualization (Leaflet.js)
- ✅ Chart generation and export (Chart.js)
- ✅ PDF report generation (html2pdf)
- ✅ Formula mode with detailed calculations
- ✅ Responsive design
- ✅ Local storage for draft recovery
- ✅ Support for multiple traffic zones (Brisbane, Gold Coast, Ipswich, Logan, etc.)

### Backend API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Service health check |
| `/report/draft` | POST | Create a report draft |
| `/report/editor/{draft_id}` | GET | Open draft in editor |
| `/docs` | GET | Swagger API documentation |

---

## Troubleshooting

### "Port already in use"
```powershell
# Use a different port
$env:PORT="8001"
python report_service.py
```

### "logo.jpeg not found"
The app will still work, but without branding. The logo is downloaded from GitHub if needed.

### External resources not loading
- Verify internet connection
- Check browser console (F12) for errors
- Ensure CDN URLs are accessible:
  - fonts.googleapis.com
  - cdnjs.cloudflare.com
  - unpkg.com
  - cdn.jsdelivr.net

### Git hooks not working
```powershell
# Reinstall hooks
powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
```

### Python service won't start
```powershell
# Verify dependencies
pip list

# Reinstall dependencies
pip install -r requirements.txt --upgrade
```

---

## Production Deployment

### Before Going Live
- [ ] Test all external resource CDNs from production network
- [ ] Set appropriate CORS headers if hosting on different domain
- [ ] Configure HOST to `0.0.0.0` for public servers
- [ ] Use HTTPS in production
- [ ] Consider using production ASGI server (Gunicorn, etc.)
- [ ] Set up monitoring and logging
- [ ] Test report generation with real traffic data

### Production Python Launch

Using Gunicorn (recommended):
```powershell
pip install gunicorn

gunicorn -w 4 -b 0.0.0.0:8000 report_service:app
```

### Nginx Reverse Proxy Configuration (Sample)
```nginx
server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## Support & Documentation

- **User Manual**: Open `manual.html` in browser
- **API Documentation**: Visit `http://localhost:8000/docs` when service is running
- **Formulas**: View detailed formulas in `index_formulas.html`
- **License**: See `LICENSE` file (Crompton Concepts proprietary)

---

## Version Information

- **Python**: 3.10+
- **FastAPI**: 0.104.1
- **Uvicorn**: 0.24.0
- **Frontend**: Vanilla JavaScript + Leaflet 1.9.4 + Chart.js 4.4.0

---

**Last Updated**: April 1, 2026
**License**: Proprietary - Crompton Concepts
