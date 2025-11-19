from fastapi import FastAPI, UploadFile, Form, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pathlib import Path
from typing import Optional, Dict, Any, List
from enum import Enum
import subprocess
import hashlib
import shutil
import uuid
import os
import time
import json
import asyncio
import httpx

app = FastAPI(title="APK Middleware Replacement Server")

# ============================================================================
# API ROUTES REGISTRY - Centralized API Management
# ============================================================================
API_ROUTES = {
    "GET /": {
        "description": "Health check and server info",
        "parameters": [],
        "auth_required": False
    },
    "GET /api_routes": {
        "description": "Get all available API routes (this endpoint)",
        "parameters": [],
        "auth_required": False
    },
    "POST /upload": {
        "description": "Upload new APK and start processing",
        "parameters": [
            {"name": "file", "type": "UploadFile", "required": True, "description": "APK file"},
            {"name": "so_files", "type": "str (JSON)", "required": True, "description": "JSON object of SO files to replace"},
            {"name": "so_architecture", "type": "str", "required": True, "description": "arm64-v8a or armeabi-v7a"},
            {"name": "pkg_name", "type": "str", "required": True, "description": "Package name"},
            {"name": "md5", "type": "str", "required": False, "description": "Pre-calculated MD5 for verification"}
        ],
        "auth_required": False
    },
    "POST /exist_pkg": {
        "description": "Process existing APK by source MD5 (reuse uploaded APK)",
        "parameters": [
            {"name": "md5", "type": "str", "required": True, "description": "Source MD5 or derived MD5 from previous processing"},
            {"name": "so_files", "type": "str (JSON)", "required": True, "description": "JSON object of SO files to replace"},
            {"name": "so_architecture", "type": "str", "required": True, "description": "arm64-v8a or armeabi-v7a"},
            {"name": "pkg_name", "type": "str", "required": True, "description": "Package name"}
        ],
        "auth_required": False
    },
    "GET /check_md5/{md5}": {
        "description": "Check if MD5 exists (source or derived) and get cache info",
        "parameters": [
            {"name": "md5", "type": "str", "required": True, "description": "MD5 to check (can be source or derived)"}
        ],
        "auth_required": False
    },
    "GET /task_status/{task_id}": {
        "description": "Get task processing status and details",
        "parameters": [
            {"name": "task_id", "type": "str", "required": True, "description": "Task UUID"}
        ],
        "auth_required": False
    },
    "GET /download/{task_id}": {
        "description": "Download processed APK by task ID",
        "parameters": [
            {"name": "task_id", "type": "str", "required": True, "description": "Task UUID"}
        ],
        "auth_required": False
    },
    "GET /download_cached/{file_md5}": {
        "description": "Download cached processed APK by MD5 (supports source and derived MD5)",
        "parameters": [
            {"name": "file_md5", "type": "str", "required": True, "description": "Source MD5 or derived MD5"},
            {"name": "so_architecture", "type": "str", "required": False, "description": "Filter by architecture"}
        ],
        "auth_required": False
    },
    "GET /index": {
        "description": "Get complete index of all processed APKs",
        "parameters": [],
        "auth_required": False
    }
}
# ============================================================================

# Configuration
ENABLE_PKGNAME_BASED_PATH = True
WORKDIR = Path("./workdir")
UPLOAD_DIR = WORKDIR / "uploads"
PROCESSED_DIR = WORKDIR / "processed"
TEMP_DIR = WORKDIR / "temp"
INDEX_FILE = WORKDIR / "index.json"

# SMB Configuration for network installation
# Set this to your SMB share path, e.g., "\\192.168.1.100\apk\"
# Leave empty to disable SMB path generation
SMB_BASE_PATH = "\\\\10.8.24.59\\a\\"  # Example: "\\\\192.168.1.100\\apk\\"
DOWNLOAD_BASE_PATH = ""  # Example: "\\\\192.168.1.100\\apk\\"

for d in [UPLOAD_DIR, PROCESSED_DIR, TEMP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Architecture mapping
ARCH_MAPPING = {
    "arm64-v8a": ["aarch64", "arm64"],
    "armeabi-v7a": ["armv7", "arm"],
}


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    filename: str
    pkg_name: str
    file_md5_before: str
    file_md5_after: Optional[str] = None
    so_md5_before: Optional[str] = None
    so_md5_after: Optional[str] = None
    so_architecture: str
    real_so_architecture: Optional[str] = None
    start_process_timestamp: Optional[float] = None
    end_process_timestamp: Optional[float] = None
    total_consume_seconds: Optional[float] = None
    signed_apk_download_path: Optional[str] = None
    smb_path: Optional[str] = None
    reason: Optional[str] = None


# In-memory task storage
tasks: Dict[str, TaskInfo] = {}


def load_index() -> Dict[str, Any]:
    """
    Load index file - returns dict with source MD5 as key
    
    New structure:
    {
        "source_md5": {
            "source_md5": "xxx",
            "derived_md5s": ["result1", "result2", ...],
            "tasks": [task1, task2, ...]
        }
    }
    
    Backward compatible: migrates old format automatically
    """
    if INDEX_FILE.exists():
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            migrated = {}
            
            for md5, entry in data.items():
                # Check if already new format
                if isinstance(entry, dict) and "source_md5" in entry and "derived_md5s" in entry:
                    migrated[md5] = entry
                # Old format: single dict task
                elif isinstance(entry, dict) and "task_id" in entry:
                    migrated[md5] = {
                        "source_md5": md5,
                        "derived_md5s": [entry.get("file_md5_after")] if entry.get("file_md5_after") else [],
                        "tasks": [entry]
                    }
                # Old format: list of tasks
                elif isinstance(entry, list):
                    derived = list(set([t.get("file_md5_after") for t in entry if t.get("file_md5_after")]))
                    migrated[md5] = {
                        "source_md5": md5,
                        "derived_md5s": derived,
                        "tasks": entry
                    }
            
            return migrated
    return {}


def save_index(index: Dict[str, Any]):
    """Save index file with source MD5 structure"""
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def find_source_md5(index: Dict[str, Any], md5: str) -> Optional[str]:
    """
    Find source MD5 from any MD5 (source or derived)
    
    Returns:
    - source_md5 if found
    - None if not found
    """
    md5_lower = md5.lower()
    
    # Check if it's a source MD5
    if md5_lower in index:
        return md5_lower
    
    # Check if it's a derived MD5
    found_sources = []
    for source_md5, entry in index.items():
        if md5_lower in entry.get("derived_md5s", []):
            found_sources.append(source_md5)
    
    # Handle edge case: MD5 collision (extremely rare)
    if len(found_sources) > 1:
        print(f"[WARNING] MD5 collision detected! {md5_lower} found in multiple sources: {found_sources}")
        print(f"[WARNING] return nothing let client upload new fiel")
        return None # 
    elif len(found_sources) == 1:
        return found_sources[0]
    
    return None


def get_source_entry(index: Dict[str, Any], md5: str) -> Optional[Dict[str, Any]]:
    """
    Get source entry from any MD5 (source or derived)
    
    Returns the complete source entry including tasks and derived MD5s
    """
    source_md5 = find_source_md5(index, md5)
    if source_md5:
        return index[source_md5]
    return None


def get_latest_cached_task(index: Dict[str, Any], file_md5: str, so_architecture: str = None) -> Optional[Dict[str, Any]]:
    """
    Get the latest cached task for given MD5 and optional architecture
    Works with both source MD5 and derived MD5
    
    Returns the most recent task entry, optionally filtered by architecture
    """
    source_entry = get_source_entry(index, file_md5)
    if not source_entry:
        return None
    
    tasks = source_entry.get("tasks", [])
    if not tasks:
        return None
    
    # Filter by architecture if specified
    if so_architecture:
        filtered = [t for t in tasks if t.get("so_architecture") == so_architecture]
        if filtered:
            # Return most recent (highest timestamp)
            return max(filtered, key=lambda x: x.get("timestamp", 0))
        return None
    
    # Return most recent task regardless of architecture
    return max(tasks, key=lambda x: x.get("timestamp", 0))


def add_task_to_index(index: Dict[str, Any], source_md5: str, task_entry: Dict[str, Any], derived_md5: Optional[str] = None):
    """
    Add a new task entry to index with source MD5 structure
    
    Args:
        source_md5: The original APK MD5
        task_entry: Task information dict
        derived_md5: The resulting APK MD5 after processing
    """
    if source_md5 not in index:
        index[source_md5] = {
            "source_md5": source_md5,
            "derived_md5s": [],
            "tasks": []
        }
    
    # Add derived MD5 if provided and not already present
    if derived_md5 and derived_md5 not in index[source_md5]["derived_md5s"]:
        index[source_md5]["derived_md5s"].append(derived_md5)
    
    # Add new task entry
    index[source_md5]["tasks"].append(task_entry)
    
    # Keep only last 10 tasks per source MD5 to prevent unbounded growth
    if len(index[source_md5]["tasks"]) > 10:
        # Sort by timestamp and keep most recent 10
        index[source_md5]["tasks"].sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        index[source_md5]["tasks"] = index[source_md5]["tasks"][:10]
        
        # Clean up derived_md5s that are no longer in tasks
        kept_derived = set()
        for task in index[source_md5]["tasks"]:
            if task.get("file_md5_after"):
                kept_derived.add(task["file_md5_after"])
        index[source_md5]["derived_md5s"] = list(kept_derived)


def md5sum(file_path: Path) -> str:
    """Calculate file MD5"""
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def md5sum_stream(file_obj) -> str:
    """Calculate MD5 from file-like object (stream)"""
    h = hashlib.md5()
    file_obj.seek(0)  # Reset to beginning
    for chunk in iter(lambda: file_obj.read(8192), b""):
        h.update(chunk)
    file_obj.seek(0)  # Reset for later use
    return h.hexdigest()


def detect_so_architecture(so_file: Path) -> Optional[str]:
    """Detect SO file architecture using 'file' command"""
    try:
        result = subprocess.run(
            ["file", str(so_file)],
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout.lower()
        
        # Check for 64-bit ARM
        if "aarch64" in output or "arm64" in output:
            return "arm64-v8a"
        # Check for 32-bit ARM
        elif "arm" in output:
            return "armeabi-v7a"
        
        return None
    except Exception as e:
        print(f"Error detecting architecture: {e}")
        return None


async def download_file(url: str, dest_path: Path) -> bool:
    """Download file from URL"""
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
        return True
    except Exception as e:
        print(f"Download error: {e}")
        return False


def run_apktool_decode(apk_path: Path, output_dir: Path) -> bool:
    """Extract APK using apktool"""
    try:
        subprocess.run(
            ["apktool", "d", "-r", "-s", str(apk_path), "-o", str(output_dir), "-f"],
            check=True,
            capture_output=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"Apktool decode error: {e.stderr.decode()}")
        return False


def run_apktool_build(extracted_dir: Path, output_apk: Path) -> bool:
    """Rebuild APK using apktool"""
    try:
        subprocess.run(
            ["apktool", "b", str(extracted_dir), "-o", str(output_apk)],
            check=True,
            capture_output=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"Apktool build error: {e.stderr.decode()}")
        return False


def run_zipalign(input_apk: Path, output_apk: Path) -> bool:
    """Align APK using zipalign"""
    try:
        subprocess.run(
            ["zipalign", "-f", "-v", "4", str(input_apk), str(output_apk)],
            check=True,
            capture_output=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"Zipalign error: {e.stderr.decode()}")
        return False


def run_apksigner(input_apk: Path, output_apk: Path) -> bool:
    """Sign APK using apksigner"""
    try:
        # Check if keystore exists, if not create a test keystore
        keystore = Path("test_keystore.jks")
        if not keystore.exists():
            subprocess.run(
                [
                    "keytool", "-genkey", "-v",
                    "-keystore", str(keystore),
                    "-alias", "testalias",
                    "-keyalg", "RSA",
                    "-keysize", "2048",
                    "-validity", "10000",
                    "-storepass", "testpass",
                    "-keypass", "testpass",
                    "-dname", "CN=Test, OU=Test, O=Test, L=Test, S=Test, C=US"
                ],
                check=True,
                capture_output=True
            )
        
        subprocess.run(
            [
                "apksigner", "sign",
                "--ks", str(keystore),
                "--ks-key-alias", "testalias",
                "--ks-pass", "pass:testpass",
                "--key-pass", "pass:testpass",
                "--in", str(input_apk),
                "--out", str(output_apk)
            ],
            check=True,
            capture_output=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"Apksigner error: {e.stderr.decode()}")
        return False


async def process_apk_task(
    task_id: str,
    apk_path: Path,
    so_files: dict,
    so_architecture: str,
    pkg_name: str,
    file_md5: str
):
    """
    Background task to process APK
    
    Args:
        so_files: Dictionary of {so_filename: download_url}
                 Example: {"libgame.so": "http://...", "libengine.so": "http://..."}
    """
    task = tasks[task_id]
    task.status = TaskStatus.PROCESSING
    task.start_process_timestamp = time.time()
    
    print(f"\n{'='*80}")
    print(f"[TASK {task_id}] Starting APK processing")
    print(f"[TASK {task_id}] Package: {pkg_name}")
    print(f"[TASK {task_id}] MD5: {file_md5}")
    print(f"[TASK {task_id}] Architecture: {so_architecture}")
    print(f"[TASK {task_id}] SO files to process: {len(so_files)}")
    print(f"{'='*80}\n")
    
    try:
        # Step 4: Create work path
        print(f"[TASK {task_id}] [Step 1/7] Creating work directory...")
        if ENABLE_PKGNAME_BASED_PATH:
            work_name = f"{pkg_name}_{file_md5}"
        else:
            work_name = file_md5
        
        work_path = TEMP_DIR / work_name
        work_path.mkdir(exist_ok=True)
        print(f"[TASK {task_id}] Work directory: {work_path}")
        
        extracted_dir = work_path / "extracted"
        
        # Step 5: Extract APK
        print(f"[TASK {task_id}] [Step 2/7] Extracting APK with apktool...")
        if not run_apktool_decode(apk_path, extracted_dir):
            raise Exception("Failed to decode APK")
        print(f"[TASK {task_id}] APK extracted successfully")
        
        # Step 6: Download and verify all SO files
        print(f"[TASK {task_id}] [Step 3/7] Downloading and verifying SO files...")
        lib_path = extracted_dir / "lib" / so_architecture
        lib_path.mkdir(parents=True, exist_ok=True)
        print(f"[TASK {task_id}] Target lib path: {lib_path}")
        
        so_replacement_info = {}
        downloaded_files = []
        
        # Check if there's at least one valid SO file to process
        valid_so_files = {k: v for k, v in so_files.items() if v and v.strip()}
        if not valid_so_files:
            raise Exception("No valid SO files to process. All URLs are empty.")
        
        print(f"[TASK {task_id}] Valid SO files: {len(valid_so_files)}/{len(so_files)}")
        
        so_index = 0
        for so_name, so_url in so_files.items():
            # Skip if URL is empty or None (compatibility for exceptional cases)
            if not so_url or not so_url.strip():
                print(f"[TASK {task_id}]   [SKIP] {so_name} (empty URL)")
                continue
            
            so_index += 1
            print(f"[TASK {task_id}]   [{so_index}/{len(valid_so_files)}] Processing: {so_name}")
            
            # Download SO file
            print(f"[TASK {task_id}]       Downloading from: {so_url[:60]}...")
            downloaded_so = work_path / f"downloaded_{so_name}"
            if not await download_file(so_url, downloaded_so):
                raise Exception(f"Failed to download SO file: {so_name} from {so_url}")
            
            file_size = downloaded_so.stat().st_size
            print(f"[TASK {task_id}]       Downloaded: {file_size:,} bytes")
            
            downloaded_files.append(downloaded_so)
            
            # Step 7: Verify architecture for this SO file
            print(f"[TASK {task_id}]       Verifying architecture...")
            real_so_arch = detect_so_architecture(downloaded_so)
            if not real_so_arch:
                raise Exception(f"Failed to detect architecture for SO file: {so_name}")
            
            if real_so_arch != so_architecture:
                raise Exception(
                    f"Architecture mismatch for {so_name}: "
                    f"requested {so_architecture}, but file is {real_so_arch}"
                )
            print(f"[TASK {task_id}]       Architecture verified: {real_so_arch}")
            
            # Check if target SO exists in APK
            existing_so = lib_path / so_name
            
            if existing_so.exists():
                so_md5_before = md5sum(existing_so)
                print(f"[TASK {task_id}]       Original SO MD5: {so_md5_before}")
            else:
                so_md5_before = "none"
                print(f"[TASK {task_id}]       Original SO not found, will be added")
            
            so_md5_after = md5sum(downloaded_so)
            print(f"[TASK {task_id}]       New SO MD5: {so_md5_after}")
            
            if so_md5_before != "none" and so_md5_before == so_md5_after:
                print(f"[TASK {task_id}]       Note: MD5 identical, but will still replace")
            
            # Store replacement info
            so_replacement_info[so_name] = {
                "md5_before": so_md5_before,
                "md5_after": so_md5_after,
                "url": so_url
            }
        
        # Step 8: All architectures verified, proceed with replacement
        print(f"[TASK {task_id}] [Step 4/7] Replacing SO files in APK...")
        replaced_count = 0
        for so_name, so_url in so_files.items():
            # Skip if URL was empty (already skipped in download phase)
            if not so_url or not so_url.strip():
                continue
            
            downloaded_so = work_path / f"downloaded_{so_name}"
            target_so = lib_path / so_name
            shutil.copy(downloaded_so, target_so)
            replaced_count += 1
            print(f"[TASK {task_id}]   [{replaced_count}/{len(valid_so_files)}] Replaced: {so_name}")
        
        print(f"[TASK {task_id}] All SO files replaced successfully")
        
        # Store SO replacement info in task
        task.real_so_architecture = so_architecture
        task.so_md5_before = json.dumps(
            {k: v["md5_before"] for k, v in so_replacement_info.items()}
        )
        task.so_md5_after = json.dumps(
            {k: v["md5_after"] for k, v in so_replacement_info.items()}
        )
        
        # Step 9: Rebuild, align, and sign APK
        print(f"[TASK {task_id}] [Step 5/7] Rebuilding APK with apktool...")
        unsigned_apk = work_path / "unsigned.apk"
        aligned_apk = work_path / "aligned.apk"
        signed_apk = PROCESSED_DIR / f"{task_id}_signed.apk"
        
        if not run_apktool_build(extracted_dir, unsigned_apk):
            raise Exception("Failed to rebuild APK")
        print(f"[TASK {task_id}] APK rebuilt: {unsigned_apk.stat().st_size:,} bytes")
        
        print(f"[TASK {task_id}] [Step 6/7] Aligning APK with zipalign...")
        if not run_zipalign(unsigned_apk, aligned_apk):
            raise Exception("Failed to align APK")
        print(f"[TASK {task_id}] APK aligned: {aligned_apk.stat().st_size:,} bytes")
        
        print(f"[TASK {task_id}] [Step 7/7] Signing APK with apksigner...")
        if not run_apksigner(aligned_apk, signed_apk):
            raise Exception("Failed to sign APK")
        print(f"[TASK {task_id}] APK signed: {signed_apk.stat().st_size:,} bytes")
        
        # Delete intermediate APK files
        print(f"[TASK {task_id}] Cleaning up intermediate files...")
        if unsigned_apk.exists():
            unsigned_apk.unlink()
        if aligned_apk.exists():
            aligned_apk.unlink()
        
        # Calculate final MD5
        print(f"[TASK {task_id}] Calculating final MD5...")
        file_md5_after = md5sum(signed_apk)
        task.file_md5_after = file_md5_after
        print(f"[TASK {task_id}] Final APK MD5: {file_md5_after}")
        
        # Step 10: Update index (add new task entry with source MD5 and derived MD5)
        print(f"[TASK {task_id}] Updating index...")
        index = load_index()
        task_entry = {
            "task_id": task_id,
            "pkg_name": pkg_name,
            "so_architecture": so_architecture,
            "signed_apk_path": str(signed_apk),
            "file_md5_after": file_md5_after,
            "timestamp": time.time()
        }
        # file_md5 is the source MD5, file_md5_after is the derived MD5
        add_task_to_index(index, file_md5, task_entry, derived_md5=file_md5_after)
        save_index(index)
        print(f"[TASK {task_id}] Index updated: source_md5={file_md5}, derived_md5={file_md5_after}")
        
        # Update task
        task.status = TaskStatus.COMPLETE
        task.end_process_timestamp = time.time()
        task.total_consume_seconds = task.end_process_timestamp - task.start_process_timestamp
        
        # Set download path (use DOWNLOAD_BASE_PATH if configured, otherwise use API endpoint)
        if DOWNLOAD_BASE_PATH:
            task.signed_apk_download_path = f"{DOWNLOAD_BASE_PATH}{task_id}_signed.apk"
        else:
            task.signed_apk_download_path = f"/download/{task_id}"
        
        # Generate SMB path if configured
        if SMB_BASE_PATH:
            # Construct full SMB path: \\ip\share\task_id_signed.apk
            smb_filename = f"{task_id}_signed.apk"
            task.smb_path = f"{SMB_BASE_PATH}{smb_filename}"
            print(f"[TASK {task_id}] SMB path: {task.smb_path}")
        
        print(f"\n{'='*80}")
        print(f"[TASK {task_id}] COMPLETED SUCCESSFULLY")
        print(f"[TASK {task_id}] Total time: {task.total_consume_seconds:.2f}s")
        print(f"[TASK {task_id}] Output: {signed_apk}")
        print(f"[TASK {task_id}] Download: {task.signed_apk_download_path}")
        print(f"{'='*80}\n")
        
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.reason = str(e)
        task.end_process_timestamp = time.time()
        if task.start_process_timestamp:
            task.total_consume_seconds = task.end_process_timestamp - task.start_process_timestamp
        
        print(f"\n{'='*80}")
        print(f"[TASK {task_id}] FAILED")
        print(f"[TASK {task_id}] Error: {str(e)}")
        print(f"[TASK {task_id}] Duration: {task.total_consume_seconds:.2f}s")
        print(f"{'='*80}\n")


@app.post("/upload")
async def upload_apk(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    so_files: str = Form(...),
    so_architecture: str = Form(...),
    pkg_name: str = Form(...),
    md5: Optional[str] = Form(None)
):
    """
    Upload APK and start processing
    
    Parameters:
    - file: APK file (required)
    - so_files: JSON string of SO files to replace
               Format: {"so_name1": "url1", "so_name2": "url2"}
               Example: {"libgame.so": "http://example.com/libgame.so"}
    - so_architecture: arm64-v8a or armeabi-v7a
    - pkg_name: Package name
    - md5: Optional pre-calculated MD5 (if provided, server verifies it matches uploaded file)
    
    Note: All uploads are processed as new packages. Results are saved to index for history tracking.
    """
    # Validate architecture
    if so_architecture not in ["arm64-v8a", "armeabi-v7a"]:
        raise HTTPException(
            status_code=400,
            detail="so_architecture must be 'arm64-v8a' or 'armeabi-v7a'"
        )
    
    # Parse and validate so_files JSON
    try:
        so_files_dict = json.loads(so_files)
        if not isinstance(so_files_dict, dict):
            raise ValueError("so_files must be a JSON object")
        if not so_files_dict:
            raise ValueError("so_files cannot be empty")
        for k, v in so_files_dict.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("so_files must be {string: string}")
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="so_files must be valid JSON string"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Generate task ID
    task_id = str(uuid.uuid4())
    
    print(f"\n[API /upload] New upload request")
    print(f"[API /upload] Request params:")
    print(f"  - filename: {file.filename}")
    print(f"  - pkg_name: {pkg_name}")
    print(f"  - so_architecture: {so_architecture}")
    print(f"  - so_files: {json.dumps(so_files_dict, indent=4)}")
    print(f"  - md5: {md5 if md5 else '(not provided)'}")
    print(f"[API /upload] Generated task_id: {task_id}")
    
    # Save uploaded file
    apk_filename = f"{task_id}_{file.filename}"
    save_path = UPLOAD_DIR / apk_filename
    
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = save_path.stat().st_size
    print(f"[API /upload] File saved: {file_size:,} bytes")

    # Calculate MD5 (use provided MD5 or calculate from file)
    if md5:
        # Validate MD5 format
        if not (len(md5) == 32 and all(c in '0123456789abcdefABCDEF' for c in md5)):
            # Clean up uploaded file
            save_path.unlink()
            raise HTTPException(
                status_code=400,
                detail="Invalid MD5 format. Must be 32 hexadecimal characters."
            )
        
        # Verify provided MD5 matches actual file MD5
        calculated_md5 = md5sum(save_path)
        md5_lower = md5.lower()
        if calculated_md5 != md5_lower:
            # Clean up uploaded file
            save_path.unlink()
            raise HTTPException(
                status_code=400,
                detail=f"MD5 mismatch. Provided: {md5_lower}, Calculated: {calculated_md5}"
            )
        file_md5 = md5_lower
    else:
        # Calculate MD5 from uploaded file
        file_md5 = md5sum(save_path)
    
    # Check if MD5 already exists (friendly hint, not blocking)
    index = load_index()
    existing_source = find_source_md5(index, file_md5)
    if existing_source:
        print(f"[API /upload] Note: MD5 already exists in cache")
        print(f"[API /upload] Hint: Could use /exist_pkg endpoint to avoid re-uploading")
        print(f"[API /upload] Proceeding with upload anyway...")
    
    # Create task (process all uploads as new packages)
    task = TaskInfo(
        task_id=task_id,
        status=TaskStatus.PENDING,
        filename=file.filename,
        pkg_name=pkg_name,
        file_md5_before=file_md5,
        so_architecture=so_architecture
    )
    tasks[task_id] = task
    
    # Start background processing
    background_tasks.add_task(
        process_apk_task,
        task_id,
        save_path,
        so_files_dict,
        so_architecture,
        pkg_name,
        file_md5
    )

    response = {
        "task_id": task_id,
        "status": "pending",
        "message": "APK processing started",
        "md5": file_md5
    }
    
    # Add hint if MD5 already exists
    if existing_source:
        response["hint"] = "This MD5 already exists in cache. Future processing can use /exist_pkg endpoint without uploading."
    
    print(f"[API /upload] Response: {json.dumps(response, indent=2)}\n")
    
    return response


@app.get("/task_status/{task_id}")
async def task_status(task_id: str):
    """Check task status"""
    print(f"\n[API /task_status] Request: task_id={task_id}")
    
    if task_id not in tasks:
        print(f"[API /task_status] Error: Task not found\n")
        raise HTTPException(status_code=404, detail="Task not found")
    
    response = tasks[task_id].model_dump(exclude_none=True)
    print(f"[API /task_status] Response: status={response.get('status')}, "
          f"progress={'completed' if response.get('status') == 'complete' else 'in progress'}\n")
    
    return response


@app.get("/download/{task_id}")
async def download_apk(task_id: str):
    """Download processed APK"""
    print(f"\n[API /download] Request: task_id={task_id}")
    
    if task_id not in tasks:
        print(f"[API /download] Error: Task not found\n")
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    if task.status != TaskStatus.COMPLETE:
        print(f"[API /download] Error: Task not complete, status={task.status}\n")
        raise HTTPException(
            status_code=400,
            detail=f"Task not complete, current status: {task.status}"
        )
    
    apk_path = PROCESSED_DIR / f"{task_id}_signed.apk"
    if not apk_path.exists():
        print(f"[API /download] Error: APK file not found\n")
        raise HTTPException(status_code=404, detail="Processed APK not found")
    
    file_size = apk_path.stat().st_size
    print(f"[API /download] Response: Sending file {task.pkg_name}_signed.apk ({file_size:,} bytes)\n")
    
    return FileResponse(
        apk_path,
        filename=f"{task.pkg_name}_signed.apk",
        media_type="application/vnd.android.package-archive"
    )


@app.get("/download_cached/{file_md5}")
async def download_cached_apk(file_md5: str, so_architecture: Optional[str] = None):
    """
    Download cached processed APK (supports both source and derived MD5)
    
    Parameters:
    - file_md5: MD5 hash (can be source MD5 or derived MD5 from previous processing)
    - so_architecture: (Optional) Filter by architecture, returns latest matching task
    
    Note: Automatically resolves derived MD5 to source MD5 and returns the latest cached result
    """
    print(f"\n[API /download_cached] Request:")
    print(f"  - file_md5: {file_md5}")
    print(f"  - so_architecture: {so_architecture if so_architecture else '(not specified)'}")
    
    index = load_index()
    
    # Get latest cached task (optionally filtered by architecture)
    cached_entry = get_latest_cached_task(index, file_md5, so_architecture)
    
    if not cached_entry:
        print(f"[API /download_cached] Error: Cached APK not found\n")
        raise HTTPException(status_code=404, detail="Cached APK not found")
    
    apk_path = Path(cached_entry["signed_apk_path"])
    
    if not apk_path.exists():
        print(f"[API /download_cached] Error: APK file not found at {apk_path}\n")
        raise HTTPException(status_code=404, detail="Cached APK file not found")
    
    file_size = apk_path.stat().st_size
    print(f"[API /download_cached] Response: Sending {cached_entry['pkg_name']}_signed.apk ({file_size:,} bytes)\n")
    
    return FileResponse(
        apk_path,
        filename=f"{cached_entry['pkg_name']}_signed.apk",
        media_type="application/vnd.android.package-archive"
    )


@app.post("/exist_pkg")
async def exist_pkg(
    background_tasks: BackgroundTasks,
    md5: str = Form(...),
    so_files: str = Form(...),
    so_architecture: str = Form(...),
    pkg_name: str = Form(...)
):
    """
    Process existing APK by MD5 (file upload not required)
    
    This endpoint accepts both source MD5 and derived MD5.
    It will automatically find the original APK and reuse it for processing.
    
    Parameters:
    - md5: MD5 hash of APK (can be source MD5 or derived MD5 from previous processing)
    - so_files: JSON string of SO files to replace
               Format: {"so_name1": "url1", "so_name2": "url2"}
               Example: {"libgame.so": "http://example.com/libgame.so"}
    - so_architecture: arm64-v8a or armeabi-v7a
    - pkg_name: Package name
    
    Workflow:
    1. Client calls /check_md5 with current APK MD5
    2. If exists, client calls this endpoint with that MD5
    3. Server finds source MD5 and reuses original APK files
    4. No upload needed - saves bandwidth and processing time
    """
    # Validate architecture
    if so_architecture not in ["arm64-v8a", "armeabi-v7a"]:
        raise HTTPException(
            status_code=400,
            detail="so_architecture must be 'arm64-v8a' or 'armeabi-v7a'"
        )
    
    # Parse and validate so_files JSON
    try:
        so_files_dict = json.loads(so_files)
        if not isinstance(so_files_dict, dict):
            raise ValueError("so_files must be a JSON object")
        if not so_files_dict:
            raise ValueError("so_files cannot be empty")
        for k, v in so_files_dict.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("so_files must be {string: string}")
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="so_files must be valid JSON string"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Validate MD5 format
    if not (len(md5) == 32 and all(c in '0123456789abcdefABCDEF' for c in md5)):
        raise HTTPException(
            status_code=400,
            detail="Invalid MD5 format. Must be 32 hexadecimal characters."
        )
    
    md5_lower = md5.lower()
    
    print(f"\n[API /exist_pkg] Processing existing APK")
    print(f"[API /exist_pkg] Input MD5: {md5_lower}")
    print(f"[API /exist_pkg] Package: {pkg_name}")
    print(f"[API /exist_pkg] Architecture: {so_architecture}")
    print(f"[API /exist_pkg] SO files count: {len(so_files_dict)}")
    
    # Find source MD5 (works with both source and derived MD5)
    index = load_index()
    source_md5 = find_source_md5(index, md5_lower)
    
    if not source_md5:
        print(f"[API /exist_pkg] MD5 not found in index (neither source nor derived)")
        raise HTTPException(
            status_code=404,
            detail=f"MD5 {md5_lower} not found in index. Use /upload endpoint for new APKs."
        )
    
    md5_type = "source" if md5_lower == source_md5 else "derived"
    print(f"[API /exist_pkg] Found {md5_type} MD5, source_md5={source_md5}")
    print(f"[API /exist_pkg] Searching for original APK with source MD5...")
    
    # Find the original APK file using source MD5
    original_apk = None
    searched_count = 0
    for apk_file in UPLOAD_DIR.glob("*.apk"):
        searched_count += 1
        file_md5_check = md5sum(apk_file)
        if file_md5_check == source_md5:
            original_apk = apk_file
            print(f"[API /exist_pkg] Found original APK: {apk_file.name}")
            print(f"[API /exist_pkg] Verified MD5 matches: {source_md5}")
            break
    
    print(f"[API /exist_pkg] Searched {searched_count} APK files in uploads directory")
    
    if not original_apk or not original_apk.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Original APK file not found for source MD5 {source_md5}. Please re-upload using /upload endpoint."
        )
    
    # Generate new task ID
    task_id = str(uuid.uuid4())
    
    # Copy original APK to new location for this task
    apk_filename = f"{task_id}_{original_apk.name.split('_', 1)[-1]}"
    save_path = UPLOAD_DIR / apk_filename
    shutil.copy(original_apk, save_path)
    print(f"[API /exist_pkg] Copied original APK to: {apk_filename}")
    
    # Create task - use source_md5 as file_md5_before
    task = TaskInfo(
        task_id=task_id,
        status=TaskStatus.PENDING,
        filename=original_apk.name,
        pkg_name=pkg_name,
        file_md5_before=source_md5,  # Use source MD5
        so_architecture=so_architecture
    )
    tasks[task_id] = task
    
    # Start background processing
    background_tasks.add_task(
        process_apk_task,
        task_id,
        save_path,
        so_files_dict,
        so_architecture,
        pkg_name,
        source_md5  # Pass source MD5 for indexing
    )

    response = {
        "task_id": task_id,
        "status": "pending",
        "message": f"APK processing started (reusing original APK, {md5_type} MD5 provided)",
        "input_md5": md5_lower,
        "source_md5": source_md5,
        "md5_type": md5_type
    }
    
    print(f"[API /exist_pkg] Response: {json.dumps(response, indent=2)}\n")
    
    return response


@app.get("/api_routes")
async def get_api_routes():
    """
    Get all available API routes with descriptions and parameters
    
    This endpoint provides a centralized view of all API endpoints,
    similar to Go's api_route pattern.
    """
    print(f"\n[API /api_routes] Request: Get all API routes")
    print(f"[API /api_routes] Response: {len(API_ROUTES)} routes\n")
    
    return {
        "total_routes": len(API_ROUTES),
        "routes": API_ROUTES,
        "server_info": {
            "title": "APK Middleware Replacement Server",
            "version": "3.0",
            "description": "Server for APK middleware replacement without root access"
        }
    }


@app.get("/index")
async def get_index():
    """Get current index with source MD5 structure"""
    print(f"\n[API /index] Request: Get full index")
    
    index = load_index()
    total_source_md5 = len(index)
    total_tasks = sum(len(entry.get("tasks", [])) for entry in index.values())
    total_derived = sum(len(entry.get("derived_md5s", [])) for entry in index.values())
    
    print(f"[API /index] Response: {total_source_md5} source MD5 entries, "
          f"{total_derived} derived MD5s, {total_tasks} total tasks\n")
    
    return {
        "total_source_md5": total_source_md5,
        "total_derived_md5": total_derived,
        "total_tasks": total_tasks,
        "index": index
    }


@app.get("/check_md5/{md5}")
async def check_md5(md5: str):
    """
    Check if MD5 exists in index (works with both source MD5 and derived MD5)
    
    Use this endpoint before deciding whether to use /upload or /exist_pkg
    
    Returns:
    - exists: boolean indicating if MD5 is in index
    - md5_type: "source" | "derived" | null
    - source_md5: the source MD5 (original APK MD5)
    - derived_md5s: list of all derived MD5s from this source
    - count: number of tasks for this source MD5
    - latest_task: most recent task info (if exists)
    - can_reuse: boolean indicating if the APK can be reused (no upload needed)
    """
    print(f"\n[API /check_md5] Request: md5={md5}")
    
    # Validate MD5 format
    if not (len(md5) == 32 and all(c in '0123456789abcdefABCDEF' for c in md5)):
        print(f"[API /check_md5] Error: Invalid MD5 format\n")
        raise HTTPException(
            status_code=400,
            detail="Invalid MD5 format. Must be 32 hexadecimal characters."
        )
    
    md5_lower = md5.lower()
    index = load_index()
    
    # Find source MD5 (works with both source and derived MD5)
    source_md5 = find_source_md5(index, md5_lower)
    
    if not source_md5:
        response = {
            "exists": False,
            "md5": md5_lower,
            "md5_type": None,
            "source_md5": None,
            "derived_md5s": [],
            "count": 0,
            "can_reuse": False
        }
        print(f"[API /check_md5] Response: exists=False\n")
        return response
    
    # Get source entry
    source_entry = index[source_md5]
    tasks_list = source_entry.get("tasks", [])
    derived_md5s = source_entry.get("derived_md5s", [])
    latest_task = max(tasks_list, key=lambda x: x.get("timestamp", 0)) if tasks_list else None
    
    # Determine MD5 type
    md5_type = "source" if md5_lower == source_md5 else "derived"
    
    response = {
        "exists": True,
        "md5": md5_lower,
        "md5_type": md5_type,
        "source_md5": source_md5,
        "derived_md5s": derived_md5s,
        "derived_count": len(derived_md5s),
        "task_count": len(tasks_list),
        "latest_task": latest_task,
        "can_reuse": True,  # Can always reuse if MD5 exists
        "message": f"Found {md5_type} MD5. Can use /exist_pkg endpoint to reuse original APK (no upload needed).",
        "usage_hint": {
            "step_1": f"Use POST /exist_pkg with md5={md5_lower} (no file upload needed)",
            "step_2": "Provide so_files, so_architecture, and pkg_name",
            "step_3": "Server will automatically find and reuse the original APK",
            "benefit": "Saves bandwidth and upload time"
        }
    }
    
    print(f"[API /check_md5] Response: exists=True, md5_type={md5_type}, "
          f"source_md5={source_md5}, count={len(tasks_list)}, "
          f"latest_task_id={latest_task.get('task_id') if latest_task else 'N/A'}\n")
    
    return response


@app.get("/")
def root():
    response = {
        "msg": "APK Middleware Replacement Server",
        "version": "3.0",
        "status": "running",
        "features": [
            "Unified API route management",
            "Source MD5 tracking with derived MD5s",
            "Smart package reuse (upload once, reprocess many times)",
            "Automatic MD5 type detection (source/derived)"
        ],
        "endpoints": {
            "api_routes": "GET /api_routes - View all available APIs",
            "check_md5": "GET /check_md5/{md5} - Check if MD5 exists (source or derived)",
            "upload": "POST /upload - Upload new APK",
            "exist_pkg": "POST /exist_pkg - Reuse existing APK (no upload needed)"
        }
    }
    print(f"\n[API /] Health check: version={response['version']}, status={response['status']}\n")
    return JSONResponse(response)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8800)
