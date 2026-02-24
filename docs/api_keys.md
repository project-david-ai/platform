# 🔐 Entities V1 — API Key Authentication & Provisioning

## Overview

Entities V1 uses a secure, user-scoped API key system to authenticate requests. Each key is uniquely tied to a user and must be included in the `X-API-Key` header for all protected endpoints.

---

## 🔧 Admin Bootstrap: First-Time Setup

On first-time deployment, you must create an **Admin user** and provision the first API key:

```
python scripts/bootstrap_admin.py
```

This will:
- Create an admin user (`ADMIN_EMAIL` from `.env`)
- Generate a **plain API key**
- Save the key to:
  - `.env` under `ENTITIES_API_KEY`
  - `admin_credentials.txt` (DO NOT COMMIT)

**Sample Output:**

```
ADMIN_USER_EMAIL=admin@example.com  
ADMIN_USER_ID=user_abc123  
ADMIN_KEY_PREFIX=ad_LV39xQKw  
ADMIN_API_KEY=ad_LV39xQKw3kYxrMNqETaFek3YzTZG5rPE
```

---

## 🔑 How to Pass Your API Key into the `Entity` Interface

The `Entity` interface provides a unified entry point to all service clients (users, assistants, tools, etc.), and supports API key injection at construction time.

You have **two clean options** for passing your API key.

---

### 1. ✅ Set via `.env` File (Default Fallback)

Place this in your `.env` file:

```env
ADMIN_API_KEY=us_8slj8dKf39x...ZmwtYeS
```

Then simply initialize:

```python
from projectdavid import Entity

client = Entity()  # uses ADMIN_API_KEY from environment
```

No need to manually pass the key if your environment is configured correctly.

---

### 2. ✅ Pass Explicitly (Overrides `.env`)

```python
from projectdavid import Entity

client = Entity(api_key="us_8slj8dKf39x...ZmwtYeS")
```

This is ideal for:

- Testing
- CI pipelines
- Dynamic switching between users

---

### 🧠 What Happens Internally?

All service clients inside `Entity` (like `.users`, `.runs`, `.assistants`, etc.) are lazily instantiated and passed the `api_key`:

```python
self._users_client = UsersClient(
    base_url=self.base_url,
    api_key=self.api_key
)
```

And from there, every request includes:

```http
X-API-Key: us_8slj8dKf39x...ZmwtYeS
```

---

### 🛡️ Best Practices

- Store your `.env` securely and **add it to `.gitignore`**
- Use `.env.dev` or `.env.docker` for different contexts
- Rotate and revoke keys as needed via `/users/{user_id}/apikeys`

```python
client.keys.create_api_key(user_id, key_name="ci-script")
```

--- 

By using the `Entity` interface, you get:
- Clean API key injection
- Shared auth across all services
- Centralized lifecycle control (e.g., `.close()` support coming soon)

```python
client.assistants.create_assistant(name="RoboDoc", model="llama3")
```



---
## 🧪 Authenticating Requests

To call any protected endpoint, include the **full API key** in the request header:

```
X-API-Key: ad_LV39xQKw3kYxrMNqETaFek3YzTZG5rPE
```

The backend:
1. Extracts the prefix (`ad_LV39xQKw`)
2. Locates a matching record in the database
3. Validates:
   - Signature (HMAC hash)
   - `is_active == True`
   - `expires_at` not passed

If any condition fails → `401 Unauthorized`.

---

## 👨‍👩‍👧‍👦 User Access Control

Each API key is bound to a specific user (`user_id`), and access to key management is **self-restricted**:

- ✅ Users can only view, create, or revoke **their own** keys.
- ❌ Users cannot access or manage keys of others.

Validation is enforced by this rule:

```
if authenticated_key.user_id != requested_user_id:
    raise HTTPException(status_code=403, detail="Not authorized.")
```

---

## 📮 API Key Management Endpoints

All key-related endpoints live under:

```
/users/{user_id}/apikeys
```

### ➕ Create a Key

```
POST /users/{user_id}/apikeys
```

**Input:**

```json
{
  "key_name": "CLI Usage",
  "expires_in_days": 30
}
```

**Output:**

```json
{
  "plain_key": "us_8slj8dKf39x...ZmwtYeS",
  "details": {
    "prefix": "us_8slj8dKf",
    "key_name": "CLI Usage",
    "created_at": "...",
    ...
  }
}
```

> ⚠️ The `plain_key` is **only shown once** — store it securely.

---

### 📃 List Keys

```
GET /users/{user_id}/apikeys
```

- Optional: `?include_inactive=true`
- Returns metadata for all user keys (never shows the full key again)

---

### 🔍 View a Specific Key (by Prefix)

```
GET /users/{user_id}/apikeys/{key_prefix}
```

- Shows detailed metadata for the matching key.
- Returns `404` if not found or doesn't belong to user.

---

### ❌ Revoke a Key

```
DELETE /users/{user_id}/apikeys/{key_prefix}
```

- Sets `is_active=False`
- Does **not** delete the record
- Key becomes unusable for future requests

---

## ✅ Validation Steps (Server-side)

When a request arrives:

1. `X-API-Key` is extracted
2. Prefix (first 8 chars) is used for DB lookup
3. Key is verified:
   - Matches stored hash (HMAC + salt)
   - Is still active (`is_active == True`)
   - Not expired (`expires_at > now`, if present)

If any step fails, the request is rejected with:

```
401 Unauthorized  
WWW-Authenticate: APIKey
```

---

## 🧠 Summary

| Role     | Permissions                          |
|----------|--------------------------------------|
| Admin    | Can create users and keys            |
| User     | Can manage only their own API keys   |

| Endpoint | Method | Purpose              |
|----------|--------|----------------------|
| `/users/{user_id}/apikeys` | POST   | Create a new API key     |
| `/users/{user_id}/apikeys` | GET    | List all API keys        |
| `/users/{user_id}/apikeys/{prefix}` | GET | Inspect a key (meta only) |
| `/users/{user_id}/apikeys/{prefix}` | DELETE | Revoke the key             |

Use this pattern to protect any public-facing or internal microservice endpoints under Entities V1.
