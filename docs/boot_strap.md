#  Development Bootstrap (Direct Scripts)

When developing locally, you can run setup scripts directly — no Docker orchestration required.  
These scripts offer complete control over admin provisioning, user creation, API key handling, and assistant setup.

---

## ️ 1. Bootstrap Admin User

**Script:** `scripts/bootstrap_admin.py`  
Creates the initial admin user and generates their API key.

###  Basic Usage

```bash
python scripts/bootstrap_admin.py
```

### 🔧 Full Options

| Flag             | Description                                                                                         |
|------------------|-----------------------------------------------------------------------------------------------------|
| `--db-url`       | Full SQLAlchemy DB URL. If omitted, uses `DATABASE_URL` or `SPECIAL_DB_URL` env vars.              |
| `--email`        | Email for the admin user (default: `admin@example.com`)                                             |
| `--name`         | Full name of the admin user (default: `Default Admin`)                                              |
| `--key-name`     | API key label (default: `Admin Bootstrap Key`)                                                      |
| `--creds-file`   | Where to write the plaintext API key (default: `./admin_credentials.txt`)                          |
| `--dotenv-path`  | Path to a `.env` file to update with generated values (default: `./.env`)                          |

###  Example (Custom Values)

```bash
python scripts/bootstrap_admin.py \
  --email "root@company.com" \
  --name "Root Admin" \
  --key-name "Root Bootstrap Key" \
  --db-url "mysql+pymysql://api_user:pw@localhost:3306/cosmic_catalyst"
```

###  Sample Output

```bash
==================================================
  Initial API Key Generated for Admin User!
  User Email: root@company.com
  User ID:    user_TsN5qkZ8AJuFd82YX7p9V2
  Key Prefix: ad_ZXLoLk
--------------------------------------------------
  PLAIN TEXT API KEY: ad_ZXLoLk4DFfVfR8pGdKeH5rTY7xM2FcB1r8cYeP9A1vC
--------------------------------------------------
  >>> Copy this key to your .env file under ADMIN_API_KEY
==================================================
Info: Admin credentials written to: ./admin_credentials.txt
Info: Admin credentials also updated in: .env
>>> IMPORTANT: Restart your API container to apply the new environment variables <<<
```

---

## 👤 2. Create Regular User

**Script:** `scripts/create_user.py`  
Generates a non-admin user and issues their API key using admin credentials.

### ✅ Basic Usage

```bash
python scripts/create_user.py
```

### 🔧 Full Options

| Flag             | Description                                                                                   |
|------------------|-----------------------------------------------------------------------------------------------|
| `--email` or `-e`| Email for the new user (default: auto-generated with timestamp)                               |
| `--name` or `-n` | Full name of the user (default: `Regular User <timestamp>`)                                   |
| `--base-url`     | API base URL to connect to (default: `http://api:9000`, or from `ASSISTANTS_BASE_URL` env)   |
| `--creds-file`   | Fallback location to load `ADMIN_API_KEY` if not in env                                       |
| `--key-name`     | Label for the user's API key (default: `Default Initial Key`)                                 |

### 🌟 Example (Custom User)

```bash
python scripts/create_user.py \
  --email "alice@example.com" \
  --name "Alice Tester" \
  --key-name "Alice Dev Key"
```

### 🖨️ Sample Output

```bash
Using Admin API Key (loaded from environment variable 'ADMIN_API_KEY') starting with: ad_ZXLo...

Initializing API client for base_workers URL: http://api:9000
API client initialized.

Attempting to create user 'Alice Tester' (alice@example.com)...

New REGULAR user created successfully:
  User ID:    user_123abc456def789ghi
  User Email: alice@example.com
  Is Admin:   False

Attempting to generate initial API key ('Alice Dev Key') for user user_123abc456def789ghi (alice@example.com)...
Calling SDK method 'create_key_for_user' on admin client for user ID user_123abc456def789ghi

==================================================
  Initial API Key Generated for Regular User (by Admin)!
  User ID:    user_123abc456def789ghi
  User Email: alice@example.com
  Key Prefix: ea_4kRsY
  Key Name:   Alice Dev Key
--------------------------------------------------
  PLAIN TEXT API KEY: ea_4kRsY7tVp9W5t3sFLoJpZJgXEFoW56cNiXJ1bVAVeGH
--------------------------------------------------
  >>> Provide this key to the regular user for their API access. <<<
==================================================
```

---

## 🤖 3. Setup Default Assistant

**Script:** `scripts/bootstrap_default_assistant.py`  
Creates a default system assistant tied to a specific user.

### ✅ Basic Usage

```bash
python scripts/bootstrap_default_assistant.py
```

### 🔧 Required Flags

| Flag         | Description                                          |
|--------------|------------------------------------------------------|
| `--api-key`  | Admin API key (or use `ADMIN_API_KEY` from env)     |
| `--user-id`  | User ID to associate the assistant with             |

### 🌟 Example

```bash
python scripts/bootstrap_default_assistant.py \
  --api-key "ad_ZXLoLk..." \
  --user-id "user_123abc456def789ghi"
```

### 🖨️ Sample Output

```bash
[DefaultAssistant] Starting assistant provisioning...
[DefaultAssistant] Using Admin API Key: ad_ZXLoLk...
[DefaultAssistant] Assigning assistant to user: user_123abc456def789ghi

Assistant created successfully:
  Assistant ID: ast_0a1b2c3d4e5f6g7h8i9j
  Assigned User: user_123abc456def789ghi
  Name: Entities Default Assistant
  Tools: ['code_interpreter', 'web_search', 'vector_store_search']

✅ Default Assistant bootstrapped.
```

---

## 🔁 Summary of Script Capabilities

| Script                          | Purpose                                  | Key Inputs                             |
|---------------------------------|------------------------------------------|----------------------------------------|
| `bootstrap_admin.py`            | Create admin user + key                  | `--email`, `--db-url`, `.env`          |
| `create_user.py`                | Create regular user + key                | `--email`, `--name`, `ADMIN_API_KEY`   |
| `bootstrap_default_assistant.py`| Setup default assistant for a user       | `--api-key`, `--user-id`               |

> 🔍 For the full setup flow (including orchestration), see:  
> 👉 [`docs/bootstrap.md`](https://github.com/frankie336/entities/blob/master/docs/bootstrap.md)

---
