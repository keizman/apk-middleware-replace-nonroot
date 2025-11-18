from fastapi import FastAPI, UploadFile, Form, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pathlib import Path
from typing import Optional, Dict, Any
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
SMB_BASE_PATH = ""  # Example: "\\\\192.168.1.100\\apk\\"
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


def load_index() -> Dict[str, list]:
    """Load index file - returns dict with MD5 as key and list of tasks as value"""
    if INDEX_FILE.exists():
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Migrate old format (single dict) to new format (list)
            migrated = {}
            for md5, entry in data.items():
                if isinstance(entry, dict):
                    # Old format: single dict, convert to list
                    migrated[md5] = [entry]
                elif isinstance(entry, list):
                    # New format: already a list
                    migrated[md5] = entry
            return migrated
    return {}


def save_index(index: Dict[str, list]):
    """Save index file - stores list of tasks for each MD5"""
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def get_latest_cached_task(index: Dict[str, list], file_md5: str, so_architecture: str = None) -> Optional[Dict[str, Any]]:
    """
    Get the latest cached task for given MD5 and optional architecture
    
    Returns the most recent task entry, optionally filtered by architecture
    """
    if file_md5 not in index:
        return None
    
    tasks = index[file_md5]
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


def add_task_to_index(index: Dict[str, list], file_md5: str, task_entry: Dict[str, Any]):
    """Add a new task entry to index"""
    if file_md5 not in index:
        index[file_md5] = []
    
    # Add new task entry
    index[file_md5].append(task_entry)
    
    # Keep only last 10 tasks per MD5 to prevent unbounded growth
    if len(index[file_md5]) > 10:
        # Sort by timestamp and keep most recent 10
        index[file_md5].sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        index[file_md5] = index[file_md5][:10]


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
    
    try:
        # Step 4: Create work path
        if ENABLE_PKGNAME_BASED_PATH:
            work_name = f"{pkg_name}_{file_md5}"
        else:
            work_name = file_md5
        
        work_path = TEMP_DIR / work_name
        work_path.mkdir(exist_ok=True)
        
        extracted_dir = work_path / "extracted"
        
        # Step 5: Extract APK
        if not run_apktool_decode(apk_path, extracted_dir):
            raise Exception("Failed to decode APK")
        
        # Step 6: Download and verify all SO files
        lib_path = extracted_dir / "lib" / so_architecture
        lib_path.mkdir(parents=True, exist_ok=True)
        
        so_replacement_info = {}
        downloaded_files = []
        
        for so_name, so_url in so_files.items():
            print(f"Processing SO file: {so_name}")
            
            # Download SO file
            downloaded_so = work_path / f"downloaded_{so_name}"
            if not await download_file(so_url, downloaded_so):
                raise Exception(f"Failed to download SO file: {so_name} from {so_url}")
            
            downloaded_files.append(downloaded_so)
            
            # Step 7: Verify architecture for this SO file
            real_so_arch = detect_so_architecture(downloaded_so)
            if not real_so_arch:
                raise Exception(f"Failed to detect architecture for SO file: {so_name}")
            
            if real_so_arch != so_architecture:
                raise Exception(
                    f"Architecture mismatch for {so_name}: "
                    f"requested {so_architecture}, but file is {real_so_arch}"
                )
            
            # Check if target SO exists in APK
            existing_so = lib_path / so_name
            
            if existing_so.exists():
                so_md5_before = md5sum(existing_so)
            else:
                so_md5_before = "none"
                print(f"Warning: {so_name} not found in original APK, will be added")
            
            so_md5_after = md5sum(downloaded_so)
            
            if so_md5_before != "none" and so_md5_before == so_md5_after:
                print(f"Note: {so_name} MD5 is identical, but will still replace")
            
            # Store replacement info
            so_replacement_info[so_name] = {
                "md5_before": so_md5_before,
                "md5_after": so_md5_after,
                "url": so_url
            }
        
        # Step 8: All architectures verified, proceed with replacement
        for so_name, so_url in so_files.items():
            downloaded_so = work_path / f"downloaded_{so_name}"
            target_so = lib_path / so_name
            shutil.copy(downloaded_so, target_so)
            print(f"Replaced: {so_name}")
        
        # Store SO replacement info in task
        task.real_so_architecture = so_architecture
        task.so_md5_before = json.dumps(
            {k: v["md5_before"] for k, v in so_replacement_info.items()}
        )
        task.so_md5_after = json.dumps(
            {k: v["md5_after"] for k, v in so_replacement_info.items()}
        )
        
        # Step 9: Rebuild, align, and sign APK
        unsigned_apk = work_path / "unsigned.apk"
        aligned_apk = work_path / "aligned.apk"
        signed_apk = PROCESSED_DIR / f"{task_id}_signed.apk"
        
        if not run_apktool_build(extracted_dir, unsigned_apk):
            raise Exception("Failed to rebuild APK")
        
        if not run_zipalign(unsigned_apk, aligned_apk):
            raise Exception("Failed to align APK")
        
        if not run_apksigner(aligned_apk, signed_apk):
            raise Exception("Failed to sign APK")
        
        # Delete intermediate APK files
        if unsigned_apk.exists():
            unsigned_apk.unlink()
        if aligned_apk.exists():
            aligned_apk.unlink()
        
        # Calculate final MD5
        file_md5_after = md5sum(signed_apk)
        task.file_md5_after = file_md5_after
        
        # Step 10: Update index (add new task entry, supports multiple tasks per MD5)
        index = load_index()
        task_entry = {
            "task_id": task_id,
            "pkg_name": pkg_name,
            "so_architecture": so_architecture,
            "signed_apk_path": str(signed_apk),
            "file_md5_after": file_md5_after,
            "timestamp": time.time()
        }
        add_task_to_index(index, file_md5, task_entry)
        save_index(index)
        
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
        
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.reason = str(e)
        task.end_process_timestamp = time.time()
        if task.start_process_timestamp:
            task.total_consume_seconds = task.end_process_timestamp - task.start_process_timestamp


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
    
    # Save uploaded file
    apk_filename = f"{task_id}_{file.filename}"
    save_path = UPLOAD_DIR / apk_filename
    
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

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

    return {
        "task_id": task_id,
        "status": "pending",
        "message": "APK processing started"
    }


@app.get("/task_status/{task_id}")
async def task_status(task_id: str):
    """Check task status"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return tasks[task_id].dict(exclude_none=True)


@app.get("/download/{task_id}")
async def download_apk(task_id: str):
    """Download processed APK"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    if task.status != TaskStatus.COMPLETE:
        raise HTTPException(
            status_code=400,
            detail=f"Task not complete, current status: {task.status}"
        )
    
    apk_path = PROCESSED_DIR / f"{task_id}_signed.apk"
    if not apk_path.exists():
        raise HTTPException(status_code=404, detail="Processed APK not found")
    
    return FileResponse(
        apk_path,
        filename=f"{task.pkg_name}_signed.apk",
        media_type="application/vnd.android.package-archive"
    )


@app.get("/download_cached/{file_md5}")
async def download_cached_apk(file_md5: str, so_architecture: Optional[str] = None):
    """
    Download cached processed APK
    
    Parameters:
    - file_md5: MD5 hash of original APK
    - so_architecture: (Optional) Filter by architecture, returns latest matching task
    """
    index = load_index()
    
    # Get latest cached task (optionally filtered by architecture)
    cached_entry = get_latest_cached_task(index, file_md5, so_architecture)
    
    if not cached_entry:
        raise HTTPException(status_code=404, detail="Cached APK not found")
    
    apk_path = Path(cached_entry["signed_apk_path"])
    
    if not apk_path.exists():
        raise HTTPException(status_code=404, detail="Cached APK file not found")
    
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
    
    Use this endpoint when APK MD5 exists in index.
    The original APK file will be retrieved from cache for processing.
    
    Parameters:
    - md5: MD5 hash of APK (required, must exist in index)
    - so_files: JSON string of SO files to replace
               Format: {"so_name1": "url1", "so_name2": "url2"}
               Example: {"libgame.so": "http://example.com/libgame.so"}
    - so_architecture: arm64-v8a or armeabi-v7a
    - pkg_name: Package name
    
    Note: This endpoint requires the APK to have been uploaded before.
    Check /index first to verify MD5 exists.
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
    
    # Check if MD5 exists in index
    index = load_index()
    if md5_lower not in index:
        raise HTTPException(
            status_code=404,
            detail=f"MD5 {md5_lower} not found in index. Use /upload endpoint for new APKs."
        )
    
    # Find the original APK file from previous uploads
    # Check in uploads directory for any APK with matching MD5
    original_apk = None
    for apk_file in UPLOAD_DIR.glob("*.apk"):
        if md5sum(apk_file) == md5_lower:
            original_apk = apk_file
            break
    
    if not original_apk or not original_apk.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Original APK file not found for MD5 {md5_lower}. Please re-upload using /upload endpoint."
        )
    
    # Generate new task ID
    task_id = str(uuid.uuid4())
    
    # Copy original APK to new location for this task
    apk_filename = f"{task_id}_{original_apk.name.split('_', 1)[-1]}"
    save_path = UPLOAD_DIR / apk_filename
    shutil.copy(original_apk, save_path)
    
    # Create task
    task = TaskInfo(
        task_id=task_id,
        status=TaskStatus.PENDING,
        filename=original_apk.name,
        pkg_name=pkg_name,
        file_md5_before=md5_lower,
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
        md5_lower
    )

    return {
        "task_id": task_id,
        "status": "pending",
        "message": "APK processing started (using existing APK from cache)",
        "md5": md5_lower
    }


@app.get("/index")
async def get_index():
    """Get current index"""
    return load_index()


@app.get("/check_md5/{md5}")
async def check_md5(md5: str):
    """
    Check if MD5 exists in index
    
    Use this endpoint before deciding whether to use /upload or /exist_pkg
    
    Returns:
    - exists: boolean indicating if MD5 is in index
    - count: number of tasks for this MD5
    - latest_task: most recent task info (if exists)
    """
    # Validate MD5 format
    if not (len(md5) == 32 and all(c in '0123456789abcdefABCDEF' for c in md5)):
        raise HTTPException(
            status_code=400,
            detail="Invalid MD5 format. Must be 32 hexadecimal characters."
        )
    
    md5_lower = md5.lower()
    index = load_index()
    
    if md5_lower not in index:
        return {
            "exists": False,
            "md5": md5_lower,
            "count": 0
        }
    
    tasks_list = index[md5_lower]
    latest_task = max(tasks_list, key=lambda x: x.get("timestamp", 0)) if tasks_list else None
    
    return {
        "exists": True,
        "md5": md5_lower,
        "count": len(tasks_list),
        "latest_task": latest_task
    }


@app.get("/")
def root():
    return JSONResponse({
        "msg": "APK Middleware Replacement Server",
        "version": "2.3",
        "status": "running"
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
