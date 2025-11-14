# APK Middleware Replacement - API Documentation

## Base URL

```
http://localhost:8000
```

## API Version

**Version**: 2.2  
**Protocol**: HTTP/HTTPS  
**Format**: JSON  
**Character Encoding**: UTF-8

---

## Endpoints Overview

| Endpoint | Method | Purpose | Use Case |
|----------|--------|---------|----------|
| `/` | GET | Health check | Verify server status |
| `/check_md5/{md5}` | GET | Check if MD5 exists | Decide which upload endpoint to use |
| `/upload` | POST | Upload new APK | When MD5 not in index |
| `/exist_pkg` | POST | Process existing APK | When MD5 in index (no file upload) |
| `/task_status/{task_id}` | GET | Get task status | Monitor processing |
| `/download/{task_id}` | GET | Download by task ID | Get specific task result |
| `/download_cached/{md5}` | GET | Download by MD5 | Get latest result for MD5 |
| `/index` | GET | View all cached entries | Browse history |

---

## Endpoints Details

### 1. Server Health Check

**Endpoint**: `GET /`

**Description**: Check if the server is running and get basic information.

**Parameters**: None

**Response**:
```json
{
  "msg": "APK Middleware Replacement Server",
  "version": "2.0",
  "status": "running"
}
```

**Status Codes**:
- `200 OK` - Server is running

---

### 2. Check MD5 Exists

**Endpoint**: `GET /check_md5/{md5}`

**Description**: Check if an APK MD5 exists in the index. Use this to decide whether to use `/upload` or `/exist_pkg`.

#### Path Parameters

| Parameter | Type | Required | Description | Validation Rules |
|-----------|------|----------|-------------|------------------|
| `md5` | String | **Yes** | MD5 hash of APK | - 32 hexadecimal characters<br>- Pattern: `^[a-f0-9]{32}$`<br>- Case-insensitive |

#### Request Example

```bash
curl http://localhost:8000/check_md5/5d41402abc4b2a76b9719d911017c592
```

#### Success Response (MD5 Exists)

**Status Code**: `200 OK`

```json
{
  "exists": true,
  "md5": "5d41402abc4b2a76b9719d911017c592",
  "count": 3,
  "latest_task": {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "pkg_name": "com.example.app",
    "so_architecture": "arm64-v8a",
    "signed_apk_path": "./workdir/processed/550e8400-..._signed.apk",
    "file_md5_after": "7d793037a0760186574b0282f2f435e7",
    "timestamp": 1699999999.123
  }
}
```

#### Success Response (MD5 Not Found)

**Status Code**: `200 OK`

```json
{
  "exists": false,
  "md5": "5d41402abc4b2a76b9719d911017c592",
  "count": 0
}
```

**Decision Logic**:
- If `exists: false` → Use `/upload` endpoint (need to upload file)
- If `exists: true` → Use `/exist_pkg` endpoint (no file upload needed)

#### Error Response

**Status Code**: `400 Bad Request`

```json
{
  "detail": "Invalid MD5 format. Must be 32 hexadecimal characters."
}
```

---

### 3. Upload New APK

**Endpoint**: `POST /upload`

**Description**: Upload a new APK file for middleware replacement processing. Use this when MD5 is not in index. Returns immediately with a task ID for status tracking.

**Content-Type**: `multipart/form-data`

#### Parameters

| Parameter | Type | Required | Description | Validation Rules |
|-----------|------|----------|-------------|------------------|
| `file` | File | **Yes** | APK file to process | - Must be a valid file<br>- File extension should be `.apk`<br>- Recommended max size: 500MB<br>- File must not be empty |
| `so_download_url` | String | **Yes** | URL to download the replacement SO file | - Must be a valid HTTP/HTTPS URL<br>- URL must be accessible<br>- Max length: 2048 characters<br>- Examples:<br>&nbsp;&nbsp;`http://example.com/lib.so`<br>&nbsp;&nbsp;`https://cdn.example.com/files/***.so` |
| `so_architecture` | String | **Yes** | Target architecture for SO file | - Must be one of:<br>&nbsp;&nbsp;`"arm64-v8a"` (64-bit ARM)<br>&nbsp;&nbsp;`"armeabi-v7a"` (32-bit ARM)<br>- Case-sensitive<br>- No other values accepted |
| `pkg_name` | String | **Yes** | Android package name | - Format: reverse domain notation<br>- Pattern: `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`<br>- Min length: 3 characters<br>- Max length: 255 characters<br>- Examples:<br>&nbsp;&nbsp;`com.example.app`<br>&nbsp;&nbsp;`com.company.product.module` |
| `md5` | String | No | Pre-calculated MD5 hash of APK | - Format: 32 hexadecimal characters<br>- Pattern: `^[a-f0-9]{32}$`<br>- Case-insensitive<br>- If provided, server verifies MD5 matches uploaded file<br>- If omitted, server calculates MD5<br>- Example: `5d41402abc4b2a76b9719d911017c592` |

#### Request Example (cURL)

```bash
curl -X POST http://localhost:8000/upload \
  -F "file=@/path/to/app.apk" \
  -F "so_download_url=https://cdn.example.com/***.so" \
  -F "so_architecture=arm64-v8a" \
  -F "pkg_name=com.example.myapp" \
  -F "md5=5d41402abc4b2a76b9719d911017c592"
```

**Note**: All uploads are processed as new packages. The `md5` parameter is optional but recommended for verification. Processing results are saved to the index for history tracking.

#### Success Response

**Status Code**: `200 OK`

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "APK processing started"
}
```

**Processing Behavior**:
- All uploads are processed as new packages (no cache hit shortcuts)
- Each successful processing is saved to index with timestamp
- Index maintains up to 10 most recent tasks per APK MD5
- MD5 verification: If `md5` is provided, server verifies it matches the uploaded file

#### Error Responses

**Status Code**: `400 Bad Request`

Invalid architecture:
```json
{
  "detail": "so_architecture must be 'arm64-v8a' or 'armeabi-v7a'"
}
```

Invalid MD5 format:
```json
{
  "detail": "Invalid MD5 format. Must be 32 hexadecimal characters."
}
```

MD5 mismatch (when md5 provided but doesn't match uploaded file):
```json
{
  "detail": "MD5 mismatch. Provided: abc123..., Calculated: def456..."
}
```

**Status Code**: `422 Unprocessable Entity`

Missing required parameters:
```json
{
  "detail": [
    {
      "loc": ["body", "so_download_url"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

### 4. Process Existing APK

**Endpoint**: `POST /exist_pkg`

**Description**: Process an APK that already exists in the index (no file upload required). Use this when MD5 is found in index. The server will use the previously uploaded APK file.

**Content-Type**: `application/x-www-form-urlencoded` or `multipart/form-data`

#### Parameters

| Parameter | Type | Required | Description | Validation Rules |
|-----------|------|----------|-------------|------------------|
| `md5` | String | **Yes** | MD5 hash of existing APK | - 32 hexadecimal characters<br>- Pattern: `^[a-f0-9]{32}$`<br>- Case-insensitive<br>- **Must exist in index** |
| `so_download_url` | String | **Yes** | URL to download the replacement SO file | - Must be a valid HTTP/HTTPS URL<br>- URL must be accessible<br>- Max length: 2048 characters |
| `so_architecture` | String | **Yes** | Target architecture for SO file | - Must be one of:<br>&nbsp;&nbsp;`"arm64-v8a"` (64-bit ARM)<br>&nbsp;&nbsp;`"armeabi-v7a"` (32-bit ARM)<br>- Case-sensitive |
| `pkg_name` | String | **Yes** | Android package name | - Format: reverse domain notation<br>- Pattern: `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`<br>- Min length: 3 characters<br>- Max length: 255 characters |

#### Request Example (cURL)

```bash
curl -X POST http://localhost:8000/exist_pkg \
  -F "md5=5d41402abc4b2a76b9719d911017c592" \
  -F "so_download_url=https://cdn.example.com/***.so" \
  -F "so_architecture=arm64-v8a" \
  -F "pkg_name=com.example.myapp"
```

**Note**: No file upload required. The server uses the original APK file from cache.

#### Success Response

**Status Code**: `200 OK`

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "APK processing started (using existing APK from cache)",
  "md5": "5d41402abc4b2a76b9719d911017c592"
}
```

**Processing Behavior**:
- Uses existing APK file from cache (no upload needed)
- Processes with new SO file and creates new task entry
- Saves result to index like normal upload
- Original APK file must exist in uploads directory

#### Error Responses

**Status Code**: `400 Bad Request`

Invalid architecture:
```json
{
  "detail": "so_architecture must be 'arm64-v8a' or 'armeabi-v7a'"
}
```

Invalid MD5 format:
```json
{
  "detail": "Invalid MD5 format. Must be 32 hexadecimal characters."
}
```

**Status Code**: `404 Not Found`

MD5 not in index:
```json
{
  "detail": "MD5 5d41402a... not found in index. Use /upload endpoint for new APKs."
}
```

Original APK file not found:
```json
{
  "detail": "Original APK file not found for MD5 5d41402a.... Please re-upload using /upload endpoint."
}
```

---

### 5. Get Task Status

**Endpoint**: `GET /task_status/{task_id}`

**Description**: Retrieve the current status and results of a processing task.

#### Path Parameters

| Parameter | Type | Required | Description | Validation Rules |
|-----------|------|----------|-------------|------------------|
| `task_id` | String | **Yes** | Task identifier from upload response | - Format: UUID v4<br>- Pattern: `^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$`<br>- Example: `550e8400-e29b-41d4-a716-446655440000` |

#### Request Example

```bash
curl http://localhost:8000/task_status/550e8400-e29b-41d4-a716-446655440000
```

#### Success Response (Pending)

**Status Code**: `200 OK`

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "filename": "app.apk",
  "pkg_name": "com.example.myapp",
  "file_md5_before": "5d41402abc4b2a76b9719d911017c592",
  "so_architecture": "arm64-v8a"
}
```

#### Success Response (Processing)

**Status Code**: `200 OK`

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "filename": "app.apk",
  "pkg_name": "com.example.myapp",
  "file_md5_before": "5d41402abc4b2a76b9719d911017c592",
  "so_architecture": "arm64-v8a",
  "start_process_timestamp": 1699999999.123
}
```

#### Success Response (Complete)

**Status Code**: `200 OK`

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "complete",
  "filename": "app.apk",
  "pkg_name": "com.example.myapp",
  "file_md5_before": "5d41402abc4b2a76b9719d911017c592",
  "file_md5_after": "7d793037a0760186574b0282f2f435e7",
  "so_md5_before": "098f6bcd4621d373cade4e832627b4f6",
  "so_md5_after": "5ebe2294ecd0e0f08eab7690d2a6ee69",
  "so_architecture": "arm64-v8a",
  "real_so_architecture": "arm64-v8a",
  "start_process_timestamp": 1699999999.123,
  "end_process_timestamp": 1700000045.456,
  "total_consume_seconds": 46.333,
  "signed_apk_download_path": "/download/550e8400-e29b-41d4-a716-446655440000"
}
```

**Response Fields**:

| Field | Type | Description | Value Range |
|-------|------|-------------|-------------|
| `task_id` | String | Task UUID | UUID v4 format |
| `status` | String | Task status | `"pending"`, `"processing"`, `"complete"`, `"failed"` |
| `filename` | String | Original APK filename | 1-255 characters |
| `pkg_name` | String | Android package name | Reverse domain notation |
| `file_md5_before` | String | APK MD5 before processing | 32 hex characters |
| `file_md5_after` | String | APK MD5 after signing | 32 hex characters (null if not complete) |
| `so_md5_before` | String | Original SO file MD5 | 32 hex characters or `"none"` |
| `so_md5_after` | String | Replacement SO file MD5 | 32 hex characters (null if not complete) |
| `so_architecture` | String | Requested architecture | `"arm64-v8a"` or `"armeabi-v7a"` |
| `real_so_architecture` | String | Detected SO architecture | `"arm64-v8a"` or `"armeabi-v7a"` (null if not detected) |
| `start_process_timestamp` | Float | Processing start time | Unix timestamp (seconds since epoch) |
| `end_process_timestamp` | Float | Processing end time | Unix timestamp (null if not complete) |
| `total_consume_seconds` | Float | Processing duration | Positive number (null if not complete) |
| `signed_apk_download_path` | String | Download path | URL path (null if not complete) |

#### Success Response (Failed)

**Status Code**: `200 OK`

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "filename": "app.apk",
  "pkg_name": "com.example.myapp",
  "file_md5_before": "5d41402abc4b2a76b9719d911017c592",
  "so_architecture": "arm64-v8a",
  "start_process_timestamp": 1699999999.123,
  "end_process_timestamp": 1700000010.456,
  "total_consume_seconds": 11.333,
  "reason": "Architecture mismatch: requested arm64-v8a, but file is armeabi-v7a"
}
```

**Common Failure Reasons**:
- `"Failed to decode APK"` - APK is corrupted, protected, or hardened
- `"Failed to download SO file"` - Network error or invalid URL
- `"Failed to detect SO architecture"` - Invalid SO file format
- `"Architecture mismatch: requested {X}, but file is {Y}"` - SO file architecture doesn't match request
- `"SO file MD5 is identical, no replacement needed"` - Downloaded SO is same as existing
- `"Failed to rebuild APK"` - APK structure error
- `"Failed to align APK"` - Alignment tool error
- `"Failed to sign APK"` - Signing tool error

#### Error Response

**Status Code**: `404 Not Found`

```json
{
  "detail": "Task not found"
}
```

---

### 4. Download Processed APK

**Endpoint**: `GET /download/{task_id}`

**Description**: Download the processed and signed APK file.

#### Path Parameters

| Parameter | Type | Required | Description | Validation Rules |
|-----------|------|----------|-------------|------------------|
| `task_id` | String | **Yes** | Task identifier | UUID v4 format |

#### Request Example

```bash
curl -o signed_app.apk http://localhost:8000/download/550e8400-e29b-41d4-a716-446655440000
```

#### Success Response

**Status Code**: `200 OK`  
**Content-Type**: `application/vnd.android.package-archive`  
**Content-Disposition**: `attachment; filename="{pkg_name}_signed.apk"`

Binary APK file content.

#### Error Responses

**Status Code**: `404 Not Found`

Task not found:
```json
{
  "detail": "Task not found"
}
```

Processed APK file not found:
```json
{
  "detail": "Processed APK not found"
}
```

**Status Code**: `400 Bad Request`

Task not complete:
```json
{
  "detail": "Task not complete, current status: processing"
}
```

---

### 5. Download Cached APK

**Endpoint**: `GET /download_cached/{file_md5}`

**Description**: Download a previously processed APK from cache using its MD5 hash. Returns the most recent task, optionally filtered by architecture.

#### Path Parameters

| Parameter | Type | Required | Description | Validation Rules |
|-----------|------|----------|-------------|------------------|
| `file_md5` | String | **Yes** | MD5 hash of original APK | - 32 hexadecimal characters<br>- Pattern: `^[a-f0-9]{32}$`<br>- Case-insensitive |

#### Query Parameters

| Parameter | Type | Required | Description | Validation Rules |
|-----------|------|----------|-------------|------------------|
| `so_architecture` | String | No | Filter by architecture | - Must be `"arm64-v8a"` or `"armeabi-v7a"`<br>- If provided, returns latest task matching this architecture<br>- If omitted, returns latest task regardless of architecture |

#### Request Example

**Basic (latest task):**
```bash
curl -o signed_app.apk http://localhost:8000/download_cached/5d41402abc4b2a76b9719d911017c592
```

**With architecture filter:**
```bash
curl -o signed_app.apk "http://localhost:8000/download_cached/5d41402abc4b2a76b9719d911017c592?so_architecture=arm64-v8a"
```

#### Success Response

**Status Code**: `200 OK`  
**Content-Type**: `application/vnd.android.package-archive`  
**Content-Disposition**: `attachment; filename="{pkg_name}_signed.apk"`

Binary APK file content.

#### Error Responses

**Status Code**: `404 Not Found`

Cached entry not found:
```json
{
  "detail": "Cached APK not found"
}
```

Cached file missing:
```json
{
  "detail": "Cached APK file not found"
}
```

---

### 6. Get Cache Index

**Endpoint**: `GET /index`

**Description**: Retrieve all cached APK entries with metadata. Each MD5 maps to a list of successful tasks (up to 10 most recent per MD5).

#### Parameters

None

#### Request Example

```bash
curl http://localhost:8000/index
```

#### Success Response

**Status Code**: `200 OK`

```json
{
  "5d41402abc4b2a76b9719d911017c592": [
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "pkg_name": "com.example.app1",
      "so_architecture": "arm64-v8a",
      "signed_apk_path": "./workdir/processed/550e8400-e29b-41d4-a716-446655440000_signed.apk",
      "file_md5_after": "7d793037a0760186574b0282f2f435e7",
      "timestamp": 1700000200.789
    },
    {
      "task_id": "440e8400-e29b-41d4-a716-446655440222",
      "pkg_name": "com.example.app1",
      "so_architecture": "arm64-v8a",
      "signed_apk_path": "./workdir/processed/440e8400-e29b-41d4-a716-446655440222_signed.apk",
      "file_md5_after": "8e904038b0760197685b1393g3g546f8",
      "timestamp": 1699999999.123
    }
  ],
  "098f6bcd4621d373cade4e832627b4f6": [
    {
      "task_id": "660e8400-e29b-41d4-a716-446655440111",
      "pkg_name": "com.example.app2",
      "so_architecture": "armeabi-v7a",
      "signed_apk_path": "./workdir/processed/660e8400-e29b-41d4-a716-446655440111_signed.apk",
      "file_md5_after": "e4d909c290d0fb1ca068ffaddf22cbd0",
      "timestamp": 1700000100.456
    }
  ]
}
```

**Index Structure**:

| Level | Field | Type | Description | Value Range |
|-------|-------|------|-------------|-------------|
| Root | Key (MD5) | String | Original APK MD5 | 32 hex characters |
| Root | Value | Array | List of successful tasks for this MD5 | Up to 10 most recent tasks |
| Task Entry | `task_id` | String | Associated task UUID | UUID v4 format |
| Task Entry | `pkg_name` | String | Package name | Reverse domain notation |
| Task Entry | `so_architecture` | String | Architecture used | `"arm64-v8a"` or `"armeabi-v7a"` |
| Task Entry | `signed_apk_path` | String | File system path | Absolute or relative path |
| Task Entry | `file_md5_after` | String | Signed APK MD5 | 32 hex characters |
| Task Entry | `timestamp` | Float | Task completion time | Unix timestamp |

**Notes**:
- Each MD5 can have multiple successful tasks (different SO files, architectures, or reprocessing)
- Only the 10 most recent tasks per MD5 are kept (older entries are automatically removed)
- Tasks are sorted by timestamp (most recent first)
- When querying cache, the latest task matching the criteria is returned

---

## Data Types and Formats

### UUID v4 Format

**Pattern**: `^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$`

**Example**: `550e8400-e29b-41d4-a716-446655440000`

### MD5 Hash Format

**Pattern**: `^[a-f0-9]{32}$`  
**Length**: Exactly 32 characters  
**Characters**: Lowercase hexadecimal (0-9, a-f)

**Example**: `5d41402abc4b2a76b9719d911017c592`

### Package Name Format

**Pattern**: `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`  
**Format**: Reverse domain notation  
**Min Length**: 3 characters  
**Max Length**: 255 characters

**Valid Examples**:
- `com.example.app`
- `com.company.product.module`
- `org.opensource.tool`
- `net.developer.myapp_v2`

**Invalid Examples**:
- `app` (missing domain)
- `Com.Example.App` (uppercase not allowed)
- `com.example.App-Name` (hyphen not allowed)
- `com.123example.app` (domain starts with number)

### URL Format

**Pattern**: Must be valid HTTP or HTTPS URL  
**Max Length**: 2048 characters  
**Protocol**: `http://` or `https://`

**Valid Examples**:
- `http://example.com/file.so`
- `https://cdn.example.com/libs/***.so`
- `https://storage.googleapis.com/bucket/file.so`

### Architecture Values

**Allowed Values**:
- `arm64-v8a` - 64-bit ARM (aarch64)
- `armeabi-v7a` - 32-bit ARM

**Case-Sensitive**: Must be exact match (lowercase)

### Task Status Values

**Allowed Values**:
- `pending` - Task created, waiting to start
- `processing` - Currently processing
- `complete` - Successfully completed
- `failed` - Processing failed

### Timestamp Format

**Type**: Float  
**Unit**: Seconds since Unix epoch (January 1, 1970 00:00:00 UTC)  
**Precision**: Milliseconds (3 decimal places)

**Example**: `1699999999.123`

---

## Rate Limits

**Current Implementation**: No rate limits

**Recommendations for Production**:
- 10 requests per minute per IP for `/upload`
- 60 requests per minute per IP for `/task_status`
- Unlimited for `/download` endpoints

---

## File Size Limits

| Parameter | Limit | Recommendation |
|-----------|-------|----------------|
| APK file size | No hard limit | < 500 MB |
| SO file size | No hard limit | < 50 MB |
| Request body | Depends on server config | Typically 500 MB |

---

## Error Codes Summary

| Status Code | Description | Common Scenarios |
|-------------|-------------|------------------|
| `200` | Success | All successful requests |
| `400` | Bad Request | Invalid parameters, task not ready |
| `404` | Not Found | Task not found, file not found |
| `422` | Unprocessable Entity | Missing required fields, validation errors |
| `500` | Internal Server Error | Server errors, tool failures |

---

## Common Workflows

### Workflow 1: Basic APK Processing

```
1. POST /upload → Get task_id
2. GET /task_status/{task_id} → Poll until status is "complete"
3. GET /download/{task_id} → Download signed APK
```

### Workflow 2: With MD5 Verification

```
1. Calculate MD5 of APK locally
2. POST /upload (with md5 parameter for verification)
3. GET /task_status/{task_id} → Poll until complete
4. GET /download/{task_id} → Download signed APK
5. (Optional) GET /index → View all processed tasks for reference
```

### Workflow 3: Query Historical Results

```
1. Check /index for previously processed APKs by MD5
2. GET /download_cached/{md5}?so_architecture=arm64-v8a → Download specific version
```

### Workflow 4: Batch Processing

```
1. POST /upload for APK 1 → task_id_1
2. POST /upload for APK 2 → task_id_2
3. POST /upload for APK 3 → task_id_3
4. Poll all task_ids concurrently
5. Download all completed APKs
```

---

## Authentication

**Current Implementation**: None (open access)

**Recommendations for Production**:
- API Key authentication via header: `X-API-Key: {key}`
- OAuth 2.0 for user-based access
- IP whitelist for trusted clients

---

## CORS Policy

**Current Implementation**: Default FastAPI CORS (same-origin)

**To enable cross-origin requests**, configure CORS middleware in server:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or specific domains
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Client Implementation Examples

### Python

```python
import httpx
import asyncio

async def process_apk(apk_path, so_url, pkg_name):
    async with httpx.AsyncClient() as client:
        # Upload
        with open(apk_path, "rb") as f:
            response = await client.post(
                "http://localhost:8000/upload",
                files={"file": f},
                data={
                    "so_download_url": so_url,
                    "so_architecture": "arm64-v8a",
                    "pkg_name": pkg_name
                }
            )
        
        result = response.json()
        task_id = result["task_id"]
        
        # Poll status
        while True:
            response = await client.get(
                f"http://localhost:8000/task_status/{task_id}"
            )
            status = response.json()
            
            if status["status"] == "complete":
                break
            elif status["status"] == "failed":
                raise Exception(status["reason"])
            
            await asyncio.sleep(2)
        
        # Download
        response = await client.get(
            f"http://localhost:8000/download/{task_id}"
        )
        with open("signed.apk", "wb") as f:
            f.write(response.content)

asyncio.run(process_apk("app.apk", "http://example.com/lib.so", "com.example.app"))
```

### JavaScript

```javascript
async function processApk(apkFile, soUrl, pkgName) {
  // Upload
  const formData = new FormData();
  formData.append('file', apkFile);
  formData.append('so_download_url', soUrl);
  formData.append('so_architecture', 'arm64-v8a');
  formData.append('pkg_name', pkgName);
  
  const uploadResponse = await fetch('http://localhost:8000/upload', {
    method: 'POST',
    body: formData
  });
  
  const { task_id } = await uploadResponse.json();
  
  // Poll status
  while (true) {
    const statusResponse = await fetch(
      `http://localhost:8000/task_status/${task_id}`
    );
    const status = await statusResponse.json();
    
    if (status.status === 'complete') {
      break;
    } else if (status.status === 'failed') {
      throw new Error(status.reason);
    }
    
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  
  // Download
  const downloadResponse = await fetch(
    `http://localhost:8000/download/${task_id}`
  );
  const blob = await downloadResponse.blob();
  
  // Save or process blob
  return blob;
}
```

### cURL

```bash
#!/bin/bash

# Upload
RESPONSE=$(curl -s -X POST http://localhost:8000/upload \
  -F "file=@app.apk" \
  -F "so_download_url=http://example.com/lib.so" \
  -F "so_architecture=arm64-v8a" \
  -F "pkg_name=com.example.app")

TASK_ID=$(echo $RESPONSE | jq -r '.task_id')
echo "Task ID: $TASK_ID"

# Poll status
while true; do
  STATUS=$(curl -s http://localhost:8000/task_status/$TASK_ID)
  STATE=$(echo $STATUS | jq -r '.status')
  
  echo "Status: $STATE"
  
  if [ "$STATE" = "complete" ]; then
    break
  elif [ "$STATE" = "failed" ]; then
    echo "Error: $(echo $STATUS | jq -r '.reason')"
    exit 1
  fi
  
  sleep 2
done

# Download
curl -o signed.apk http://localhost:8000/download/$TASK_ID
echo "Downloaded: signed.apk"
```

---

## Best Practices

### 1. Provide MD5 for Verification

Calculate MD5 locally and include it in the upload request to enable server-side verification and ensure file integrity.

### 2. Poll Responsibly

When polling task status:
- Use 2-5 second intervals
- Implement exponential backoff for long-running tasks
- Set a reasonable timeout (e.g., 5 minutes)

### 3. Handle Errors Gracefully

Always check the `status` field and handle `failed` state with `reason` message.

### 4. Validate Parameters Client-Side

Validate parameters before sending to reduce unnecessary requests:
- Check APK file exists and is not empty
- Validate URL format
- Verify architecture value
- Validate package name format

### 5. Store Task IDs and Use Index

Keep track of task IDs for later reference. Use `/index` endpoint to query historical processing results and `/download_cached` to retrieve previously processed APKs.

---

## Changelog

**Version 2.1** (Current)
- Removed cache hit shortcuts from `/upload` endpoint
- All uploads now processed as new packages for easier updates
- Index maintains history of multiple successful tasks per APK MD5
- Added architecture filtering for `/download_cached` endpoint
- Improved index structure to support task history (up to 10 per MD5)

**Version 2.0**
- Complete API rewrite
- Added task status tracking
- Added architecture verification
- Improved error handling
- Added index-based history tracking

---

## Support

For issues or questions:
- Check `QUICKSTART.md` for quick solutions
- Review `USAGE_EXAMPLE.md` for examples
- Read `ARCHITECTURE.md` for system details
- Run `test_api.py` to verify setup

