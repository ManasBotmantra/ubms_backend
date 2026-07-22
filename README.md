# UBMS Backend — Azure Functions API

**Utility Bills Management System** — Python-based HTTP API layer built on **Azure Functions v2**, backed by **PostgreSQL**.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Azure Functions (Python v2 worker) |
| Database | PostgreSQL via `psycopg2-binary` |
| Auth | JWT (`PyJWT`) + `bcrypt` password hashing |
| Excel parsing | `openpyxl` |
| Reports | Custom SQL aggregations |

---

## Project Structure

```
ubms_backend/
├── common_functions/          # Shared utilities (auth, db, response, audit, config)
├── admin_functions/           # Domain mapping functions (f_* layer)
├── prerequisite/              # Design spec workbook & sample data
├── <endpoint_name>/           # One folder per API endpoint
│   ├── __init__.py            # HTTP handler
│   └── function.json          # Azure Functions binding/route
├── host.json                  # Azure Functions host config
├── requirements.txt           # Python dependencies
└── .env.example               # Environment variable template
```

There are **42 API endpoints**, each in its own folder at the repo root (e.g. `create_account_master/`, `filter_bill/`, `approve_bill_unit_details/`, `get_monthly_bill_report/`, etc.).

---

## Getting Started

### Prerequisites

- Python 3.11+
- [Azure Functions Core Tools v4](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local)
- PostgreSQL database (local or remote)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/ManasBotmantra/ubms_backend.git
cd ubms_backend

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env with your database credentials and JWT secret

# 5. Start the Azure Functions local host
func start
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values. **Never commit `.env`.**

| Variable | Description |
|---|---|
| `DB_HOST` | PostgreSQL host |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `DB_PORT` | PostgreSQL port (default: `5432`) |
| `SCHEMANAME` | Schema name (default: `public`) |
| `JWT_SECRET_KEY` | Secret key for signing JWT tokens |
| `ACCESS_TOKEN_EXPIRY_SECONDS` | Access token lifetime in seconds |

---

## API Architecture

Every endpoint follows a consistent two-layer pattern:

1. **API Handler** (`__init__.py`) — validates auth token → validates input → RBAC check → calls mapping function → builds response → logs audit trail.
2. **Mapping Function** (`admin_functions/f_*`) — performs DB operations and returns a `(status_code, description, error, body)` tuple.

### RBAC Roles
- `Administrator` — full access to all master data and management APIs
- `Unit Accountant` — bill approval/rejection/resolution for assigned units
- `Leadership` — read-only dashboards and reports

### Status Codes

| Code | Meaning |
|---|---|
| 200 | Success |
| 401 | Unauthorized (missing/invalid/expired token) |
| 402 | Invalid Input |
| 403 | Authorization Failure |
| 404 | No Data Found |
| 409 | Duplicate / Conflict |
| 500 | System Exception |

---

## License

Private project. All rights reserved.