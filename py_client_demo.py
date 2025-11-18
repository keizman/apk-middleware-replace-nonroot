"""
APK Middleware Replacement Client Demo

This demonstrates how to interact with the APK processing server.
"""

import httpx
import asyncio
import time
from pathlib import Path


class APKProcessClient:
    """Client for APK Middleware Replacement Server"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=600.0)
    
    async def check_md5(self, md5: str):
        """
        Check if MD5 exists in index
        
        Returns:
        - exists: boolean
        - count: number of tasks for this MD5
        - latest_task: most recent task info (if exists)
        """
        url = f"{self.base_url}/check_md5/{md5}"
        response = await self.client.get(url)
        response.raise_for_status()
        return response.json()
    
    async def upload_apk(
        self,
        apk_path: str,
        so_files: dict,
        so_architecture: str,
        pkg_name: str,
        md5: str = None
    ):
        """
        Upload new APK for processing (use when MD5 not in index)
        
        Args:
            apk_path: Path to APK file
            so_files: Dictionary of SO files to replace
                     Format: {"so_name1": "url1", "so_name2": "url2"}
                     Example: {"libgame.so": "http://example.com/libgame.so"}
                     Note: Empty/null URLs are automatically skipped
                           At least one valid URL is required
                           Any download failure causes entire task to fail
            so_architecture: arm64-v8a or armeabi-v7a
            pkg_name: Package name
            md5: Optional pre-calculated MD5
        """
        import json
        url = f"{self.base_url}/upload"
        
        with open(apk_path, "rb") as f:
            files = {"file": (Path(apk_path).name, f, "application/vnd.android.package-archive")}
            data = {
                "so_files": json.dumps(so_files),
                "so_architecture": so_architecture,
                "pkg_name": pkg_name,
            }
            if md5:
                data["md5"] = md5
            
            response = await self.client.post(url, files=files, data=data)
            response.raise_for_status()
            return response.json()
    
    async def process_existing_apk(
        self,
        md5: str,
        so_files: dict,
        so_architecture: str,
        pkg_name: str
    ):
        """
        Process existing APK (use when MD5 exists in index)
        No file upload required
        
        Args:
            md5: MD5 hash of the APK (must exist in index)
            so_files: Dictionary of SO files to replace
                     Format: {"so_name1": "url1", "so_name2": "url2"}
                     Example: {"libgame.so": "http://example.com/libgame.so"}
                     Note: Empty/null URLs are automatically skipped
                           At least one valid URL is required
                           Any download failure causes entire task to fail
            so_architecture: arm64-v8a or armeabi-v7a
            pkg_name: Package name
        """
        import json
        url = f"{self.base_url}/exist_pkg"
        
        data = {
            "md5": md5,
            "so_files": json.dumps(so_files),
            "so_architecture": so_architecture,
            "pkg_name": pkg_name,
        }
        
        response = await self.client.post(url, data=data)
        response.raise_for_status()
        return response.json()
    
    async def smart_upload(
        self,
        apk_path: str,
        so_files: dict,
        so_architecture: str,
        pkg_name: str
    ):
        """
        Smart upload: Calculate MD5, check if exists, then choose appropriate endpoint
        
        This is the recommended way to upload APKs.
        
        Args:
            apk_path: Path to APK file
            so_files: Dictionary of SO files to replace
                     Format: {"so_name1": "url1", "so_name2": "url2"}
                     Example: {"libgame.so": "http://example.com/libgame.so"}
            so_architecture: arm64-v8a or armeabi-v7a
            pkg_name: Package name
        """
        import hashlib
        
        # Calculate MD5
        print("Calculating MD5...")
        with open(apk_path, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
        print(f"MD5: {md5}")
        
        # Check if MD5 exists
        print("Checking if APK exists in index...")
        check_result = await self.check_md5(md5)
        
        if check_result["exists"]:
            print(f"MD5 found in index ({check_result['count']} previous tasks)")
            print("Using /exist_pkg endpoint (no file upload needed)...")
            return await self.process_existing_apk(
                md5=md5,
                so_files=so_files,
                so_architecture=so_architecture,
                pkg_name=pkg_name
            )
        else:
            print("MD5 not found in index")
            print("Using /upload endpoint (uploading file)...")
            return await self.upload_apk(
                apk_path=apk_path,
                so_files=so_files,
                so_architecture=so_architecture,
                pkg_name=pkg_name,
                md5=md5
            )
    
    async def get_task_status(self, task_id: str):
        """Get task status"""
        url = f"{self.base_url}/task_status/{task_id}"
        response = await self.client.get(url)
        response.raise_for_status()
        return response.json()
    
    async def download_apk(self, task_id: str, output_path: str):
        """Download processed APK"""
        url = f"{self.base_url}/download/{task_id}"
        
        async with self.client.stream("GET", url) as response:
            response.raise_for_status()
            with open(output_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    f.write(chunk)
    
    async def wait_for_completion(self, task_id: str, poll_interval: int = 2):
        """Wait for task to complete"""
        while True:
            status = await self.get_task_status(task_id)
            print(f"Status: {status['status']}")
            
            if status["status"] == "complete":
                return status
            elif status["status"] == "failed":
                raise Exception(f"Task failed: {status.get('reason', 'Unknown error')}")
            
            await asyncio.sleep(poll_interval)
    
    async def close(self):
        """Close client"""
        await self.client.aclose()


async def example_smart_upload():
    """
    Example 1: Smart Upload (Recommended)
    
    Automatically checks if MD5 exists and chooses the right endpoint.
    Saves bandwidth by not uploading file if it exists.
    """
    client = APKProcessClient("http://localhost:8000")
    
    try:
        print("=== Smart Upload Example ===\n")
        
        # Use smart_upload - it handles everything
        result = await client.smart_upload(
            apk_path="./test.apk",
            so_files={
                "libexample1.so": "http://example.com/libexample1.so",
                "libexample2.so": "http://example.com/libexample2.so"
            },
            so_architecture="arm64-v8a",
            pkg_name="com.example.app"
        )
        
        task_id = result["task_id"]
        print(f"\nTask ID: {task_id}")
        
        # Wait for completion
        print("\nWaiting for processing...")
        final_status = await client.wait_for_completion(task_id)
        
        print("\n=== Processing Complete ===")
        print(f"File MD5 before: {final_status['file_md5_before']}")
        print(f"File MD5 after: {final_status['file_md5_after']}")
        print(f"SO MD5 before: {final_status['so_md5_before']}")
        print(f"SO MD5 after: {final_status['so_md5_after']}")
        print(f"Architecture: {final_status['real_so_architecture']}")
        print(f"Time consumed: {final_status['total_consume_seconds']:.2f}s")
        
        # Download processed APK or use SMB path
        if final_status.get('smb_path'):
            print(f"\n=== SMB Network Path Available ===")
            print(f"SMB Path: {final_status['smb_path']}")
            print(f"\nYou can install directly via ADB:")
            print(f"  adb install {final_status['smb_path']}")
            print("\nOr download first:")
        else:
            print("\nNo SMB path configured. Downloading...")
        
        await client.download_apk(task_id, "./output_signed.apk")
        print("Download complete: ./output_signed.apk")
        
    finally:
        await client.close()


async def example_manual_check():
    """
    Example 2: Manual MD5 Check
    
    Shows how to manually check MD5 and choose endpoint.
    Gives you more control over the process.
    """
    client = APKProcessClient("http://localhost:8000")
    
    try:
        import hashlib
        
        print("=== Manual MD5 Check Example ===\n")
        
        apk_path = "./test.apk"
        
        # Step 1: Calculate MD5
        print("Step 1: Calculating MD5...")
        with open(apk_path, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
        print(f"MD5: {md5}")
        
        # Step 2: Check if exists
        print("\nStep 2: Checking if MD5 exists in index...")
        check_result = await client.check_md5(md5)
        
        if check_result["exists"]:
            print(f"✓ MD5 found! ({check_result['count']} previous tasks)")
            print(f"Latest task: {check_result['latest_task']['task_id']}")
            
            # Use exist_pkg endpoint
            print("\nStep 3: Using /exist_pkg (no file upload)...")
            result = await client.process_existing_apk(
                md5=md5,
                so_files={
                    "libexample1.so": "http://example.com/libexample1.so",
                    "libexample2.so": "http://example.com/libexample2.so"
                },
                so_architecture="arm64-v8a",
                pkg_name="com.example.app"
            )
        else:
            print("✗ MD5 not found in index")
            
            # Use upload endpoint
            print("\nStep 3: Using /upload (uploading file)...")
            result = await client.upload_apk(
                apk_path=apk_path,
                so_files={
                    "libexample1.so": "http://example.com/libexample1.so",
                    "libexample2.so": "http://example.com/libexample2.so"
                },
                so_architecture="arm64-v8a",
                pkg_name="com.example.app",
                md5=md5
            )
        
        task_id = result["task_id"]
        print(f"\nTask ID: {task_id}")
        
        # Wait for completion
        print("\nStep 4: Waiting for processing...")
        final_status = await client.wait_for_completion(task_id)
        
        print("\n=== Complete ===")
        print(f"Time consumed: {final_status['total_consume_seconds']:.2f}s")
        
    finally:
        await client.close()


async def example_existing_apk_only():
    """
    Example 3: Process Existing APK (no file upload)
    
    Use this when you know the APK is already in the system.
    """
    client = APKProcessClient("http://localhost:8000")
    
    try:
        print("=== Process Existing APK Example ===\n")
        
        # Known MD5 from previous upload
        md5 = "5d41402abc4b2a76b9719d911017c592"
        
        print(f"Using existing APK with MD5: {md5}")
        print("No file upload required!\n")
        
        # Process with new SO files
        result = await client.process_existing_apk(
            md5=md5,
            so_files={
                "libexample1.so": "http://example.com/libexample1-v2.so",
                "libexample2.so": "http://example.com/libexample2-v2.so"
            },
            so_architecture="arm64-v8a",
            pkg_name="com.example.app"
        )
        
        print(f"Task ID: {result['task_id']}")
        print(f"Message: {result['message']}")
        
    finally:
        await client.close()


async def example_smb_network_install():
    """
    Example 4: SMB Network Installation
    
    Demonstrates direct APK installation via SMB network path.
    No need to download APK - install directly from network share.
    """
    import subprocess
    
    client = APKProcessClient("http://localhost:8000")
    
    try:
        print("=== SMB Network Installation Example ===\n")
        
        # Process APK using smart_upload
        result = await client.smart_upload(
            apk_path="./test.apk",
            so_files={
                "libexample1.so": "http://example.com/libexample1.so",
                "libexample2.so": "http://example.com/libexample2.so"
            },
            so_architecture="arm64-v8a",
            pkg_name="com.example.app"
        )
        
        task_id = result["task_id"]
        print(f"\nTask ID: {task_id}")
        
        # Wait for completion
        print("\nWaiting for processing...")
        final_status = await client.wait_for_completion(task_id)
        
        print("\n=== Processing Complete ===")
        print(f"Time consumed: {final_status['total_consume_seconds']:.2f}s")
        
        # Check if SMB path is available
        if final_status.get('smb_path'):
            smb_path = final_status['smb_path']
            print(f"\n{'='*60}")
            print("SMB Network Path Available!")
            print(f"{'='*60}")
            print(f"\nSMB Path: {smb_path}")
            print(f"\n{'='*60}")
            print("Installation Options:")
            print(f"{'='*60}")
            print("\nOption 1: Direct ADB Install (Recommended)")
            print(f"  adb install {smb_path}")
            print("\nOption 2: ADB Install with Auto-Reinstall on Signature Mismatch")
            print(f"  adb install -r {smb_path} || (adb uninstall com.example.app && adb install {smb_path})")
            print("\n" + "="*60)
            
            # Try to install directly via ADB
            print("\nAttempting direct installation via ADB...")
            try:
                result = subprocess.run(
                    ["adb", "install", "-r", smb_path],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode == 0:
                    print("✓ Installation successful!")
                elif "signatures do not match" in result.stderr:
                    print("⚠ Signature mismatch detected. Uninstalling old version...")
                    subprocess.run(
                        ["adb", "uninstall", "com.example.app"],
                        capture_output=True,
                        timeout=30
                    )
                    print("Installing fresh copy...")
                    result = subprocess.run(
                        ["adb", "install", smb_path],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    if result.returncode == 0:
                        print("✓ Installation successful!")
                    else:
                        print(f"✗ Installation failed: {result.stderr}")
                else:
                    print(f"✗ Installation failed: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                print("✗ Installation timed out")
            except FileNotFoundError:
                print("✗ ADB not found. Please ensure ADB is in your PATH")
                print(f"\nManual installation command:")
                print(f"  adb install {smb_path}")
        else:
            print("\n⚠ SMB path not configured on server")
            print("To enable SMB installation:")
            print('1. Set SMB_BASE_PATH in server configuration')
            print('   Example: SMB_BASE_PATH = "\\\\\\\\192.168.1.100\\\\apk\\\\"')
            print('2. Ensure processed APK directory is shared via SMB')
            print('3. Restart server')
            print("\nFalling back to download...")
            await client.download_apk(task_id, "./output_signed.apk")
            print("Download complete: ./output_signed.apk")
        
    finally:
        await client.close()


async def example_batch_processing():
    """
    Example 5: Batch Processing with Smart Upload
    
    Process multiple APKs efficiently using smart_upload.
    """
    client = APKProcessClient("http://localhost:8000")
    
    print("=== Batch Processing Example ===\n")
    
    apks = [
        {
            "path": "./app1.apk",
            "pkg_name": "com.example.app1",
            "arch": "arm64-v8a"
        },
        {
            "path": "./app2.apk",
            "pkg_name": "com.example.app2",
            "arch": "armeabi-v7a"
        },
        {
            "path": "./app3.apk",
            "pkg_name": "com.example.app3",
            "arch": "arm64-v8a"
        },
    ]
    
    # SO files to replace
    so_files = {
        "libexample1.so": "http://example.com/libexample1.so",
        "libexample2.so": "http://example.com/libexample2.so"
    }
    
    try:
        task_ids = []
        
        # Process all APKs using smart_upload
        for i, apk_info in enumerate(apks, 1):
            print(f"\n[{i}/{len(apks)}] Processing {apk_info['pkg_name']}...")
            print("-" * 50)
            
            result = await client.smart_upload(
                apk_path=apk_info["path"],
                so_files=so_files,
                so_architecture=apk_info["arch"],
                pkg_name=apk_info["pkg_name"]
            )
            
            task_ids.append(result["task_id"])
        
        print("\n" + "=" * 50)
        print("All APKs submitted. Waiting for completion...")
        print("=" * 50)
        
        # Wait for all tasks
        for i, task_id in enumerate(task_ids, 1):
            print(f"\n[{i}/{len(task_ids)}] Waiting for task {task_id}...")
            await client.wait_for_completion(task_id)
            print(f"✓ Task {task_id} complete!")
        
        print("\n" + "=" * 50)
        print("All tasks completed!")
        print("=" * 50)
        
    finally:
        await client.close()


def sync_example():
    """
    Example 6: Synchronous Version using requests
    
    For those who prefer sync code or need to use it in sync context.
    """
    import requests
    import hashlib
    
    print("=== Synchronous Example ===\n")
    
    url = "http://localhost:8000"
    apk_path = "./test.apk"
    
    # Calculate MD5
    print("Calculating MD5...")
    with open(apk_path, "rb") as f:
        md5 = hashlib.md5(f.read()).hexdigest()
    print(f"MD5: {md5}")
    
    # Check if exists
    print("\nChecking if MD5 exists...")
    response = requests.get(f"{url}/check_md5/{md5}")
    check_result = response.json()
    
    if check_result["exists"]:
        print(f"MD5 found! Using /exist_pkg...")
        
        # Use exist_pkg
        import json
        data = {
            "md5": md5,
            "so_files": json.dumps({
                "libexample1.so": "http://example.com/libexample1.so",
                "libexample2.so": "http://example.com/libexample2.so"
            }),
            "so_architecture": "arm64-v8a",
            "pkg_name": "com.example.app"
        }
        response = requests.post(f"{url}/exist_pkg", data=data)
        result = response.json()
    else:
        print("MD5 not found. Using /upload...")
        
        # Upload APK
        import json
        with open(apk_path, "rb") as f:
            files = {"file": ("test.apk", f, "application/vnd.android.package-archive")}
            data = {
                "so_files": json.dumps({
                    "libexample1.so": "http://example.com/libexample1.so",
                    "libexample2.so": "http://example.com/libexample2.so"
                }),
                "so_architecture": "arm64-v8a",
                "pkg_name": "com.example.app",
                "md5": md5
            }
            response = requests.post(f"{url}/upload", files=files, data=data)
            result = response.json()
    
    task_id = result["task_id"]
    print(f"\nTask ID: {task_id}")
    
    # Poll for status
    print("\nWaiting for completion...")
    while True:
        response = requests.get(f"{url}/task_status/{task_id}")
        status = response.json()
        print(f"Status: {status['status']}")
        
        if status["status"] == "complete":
            print("\n=== Processing complete! ===")
            print(f"Time: {status['total_consume_seconds']:.2f}s")
            print(f"Download: {status['signed_apk_download_path']}")
            break
        elif status["status"] == "failed":
            print(f"\n✗ Failed: {status['reason']}")
            break
        
        time.sleep(2)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("APK Middleware Replacement - Client Examples")
    print("=" * 60 + "\n")
    
    print("Available examples:")
    print("  1. example_smart_upload()         - Recommended: Auto-detect and choose endpoint")
    print("  2. example_manual_check()         - Manual control over MD5 check")
    print("  3. example_existing_apk_only()    - Process existing APK (no upload)")
    print("  4. example_smb_network_install()  - SMB network installation (no download)")
    print("  5. example_batch_processing()     - Process multiple APKs")
    print("  6. sync_example()                 - Synchronous version\n")
    
    # Run the recommended example
    asyncio.run(example_smart_upload())
    
    # To run other examples, uncomment:
    # asyncio.run(example_manual_check())
    # asyncio.run(example_existing_apk_only())
    # asyncio.run(example_smb_network_install())
    # asyncio.run(example_batch_processing())
    # sync_example()

