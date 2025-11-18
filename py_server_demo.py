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
        
        # Step 10: Update index (add new task entry, supports multiple tasks per MD5)
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
        "message": "APK processing started"
    }
    
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
    Download cached processed APK
    
    Parameters:
    - file_md5: MD5 hash of original APK
    - so_architecture: (Optional) Filter by architecture, returns latest matching task
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
    
    print(f"\n[API /exist_pkg] Processing existing APK")
    print(f"[API /exist_pkg] MD5: {md5_lower}")
    print(f"[API /exist_pkg] Package: {pkg_name}")
    print(f"[API /exist_pkg] Architecture: {so_architecture}")
    print(f"[API /exist_pkg] SO files count: {len(so_files_dict)}")
    
    # Check if MD5 exists in index
    index = load_index()
    if md5_lower not in index:
        print(f"[API /exist_pkg] MD5 not found in index")
        raise HTTPException(
            status_code=404,
            detail=f"MD5 {md5_lower} not found in index. Use /upload endpoint for new APKs."
        )
    
    print(f"[API /exist_pkg] MD5 found in index, searching for original APK...")
    
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

    response = {
        "task_id": task_id,
        "status": "pending",
        "message": "APK processing started (using existing APK from cache)",
        "md5": md5_lower
    }
    
    print(f"[API /exist_pkg] Response: {json.dumps(response, indent=2)}\n")
    
    return response


@app.get("/index")
async def get_index():
    """Get current index"""
    print(f"\n[API /index] Request: Get full index")
    
    index = load_index()
    total_md5 = len(index)
    total_tasks = sum(len(tasks) for tasks in index.values())
    
    print(f"[API /index] Response: {total_md5} MD5 entries, {total_tasks} total tasks\n")
    
    return index


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
    
    if md5_lower not in index:
        response = {
            "exists": False,
            "md5": md5_lower,
            "count": 0
        }
        print(f"[API /check_md5] Response: exists=False\n")
        return response
    
    tasks_list = index[md5_lower]
    latest_task = max(tasks_list, key=lambda x: x.get("timestamp", 0)) if tasks_list else None
    
    response = {
        "exists": True,
        "md5": md5_lower,
        "count": len(tasks_list),
        "latest_task": latest_task
    }
    
    print(f"[API /check_md5] Response: exists=True, count={len(tasks_list)}, "
          f"latest_task_id={latest_task.get('task_id') if latest_task else 'N/A'}\n")
    
    return response


@app.get("/")
def root():
    response = {
        "msg": "APK Middleware Replacement Server",
        "version": "2.3",
        "status": "running"
    }
    print(f"\n[API /] Health check: version={response['version']}, status={response['status']}\n")
    return JSONResponse(response)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8800)
