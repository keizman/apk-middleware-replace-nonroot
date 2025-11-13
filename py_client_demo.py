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
    
    async def upload_apk(
        self,
        apk_path: str,
        so_download_url: str,
        so_architecture: str,
        pkg_name: str,
        md5: str = None
    ):
        """Upload APK for processing"""
        url = f"{self.base_url}/upload"
        
        with open(apk_path, "rb") as f:
            files = {"file": (Path(apk_path).name, f, "application/vnd.android.package-archive")}
            data = {
                "so_download_url": so_download_url,
                "so_architecture": so_architecture,
                "pkg_name": pkg_name,
            }
            if md5:
                data["md5"] = md5
            
            response = await self.client.post(url, files=files, data=data)
            response.raise_for_status()
            return response.json()
    
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


async def example_basic_usage():
    """Example: Basic APK processing"""
    client = APKProcessClient("http://localhost:8000")
    
    try:
        # Upload APK
        print("Uploading APK...")
        result = await client.upload_apk(
            apk_path="./test.apk",
            so_download_url="http://example.com/libranger-jni.so",
            so_architecture="arm64-v8a",
            pkg_name="com.example.app"
        )
        
        task_id = result["task_id"]
        print(f"Task ID: {task_id}")
        
        # Check if cached
        if result.get("cached"):
            print("Using cached result!")
            print(f"Download: {result['signed_apk_download_path']}")
            return
        
        # Wait for completion
        print("Waiting for processing...")
        final_status = await client.wait_for_completion(task_id)
        
        print("\nProcessing complete!")
        print(f"File MD5 before: {final_status['file_md5_before']}")
        print(f"File MD5 after: {final_status['file_md5_after']}")
        print(f"SO MD5 before: {final_status['so_md5_before']}")
        print(f"SO MD5 after: {final_status['so_md5_after']}")
        print(f"Architecture: {final_status['real_so_architecture']}")
        print(f"Time consumed: {final_status['total_consume_seconds']:.2f}s")
        
        # Download processed APK
        print("\nDownloading processed APK...")
        await client.download_apk(task_id, "./output_signed.apk")
        print("Download complete: ./output_signed.apk")
        
    finally:
        await client.close()


async def example_with_md5_check():
    """Example: Upload with pre-calculated MD5"""
    client = APKProcessClient("http://localhost:8000")
    
    try:
        # Pre-calculate MD5
        import hashlib
        apk_path = "./test.apk"
        
        print("Calculating MD5...")
        with open(apk_path, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
        print(f"MD5: {md5}")
        
        # Upload with MD5
        result = await client.upload_apk(
            apk_path=apk_path,
            so_download_url="http://example.com/libranger-jni.so",
            so_architecture="arm64-v8a",
            pkg_name="com.example.app",
            md5=md5
        )
        
        print(f"Task ID: {result['task_id']}")
        print(f"Cached: {result.get('cached', False)}")
        
    finally:
        await client.close()


async def example_batch_processing():
    """Example: Process multiple APKs"""
    client = APKProcessClient("http://localhost:8000")
    
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
    ]
    
    so_url = "http://example.com/libranger-jni.so"
    
    try:
        task_ids = []
        
        # Upload all APKs
        for apk_info in apks:
            print(f"Uploading {apk_info['pkg_name']}...")
            result = await client.upload_apk(
                apk_path=apk_info["path"],
                so_download_url=so_url,
                so_architecture=apk_info["arch"],
                pkg_name=apk_info["pkg_name"]
            )
            
            if not result.get("cached"):
                task_ids.append(result["task_id"])
        
        # Wait for all tasks
        for task_id in task_ids:
            print(f"Waiting for {task_id}...")
            await client.wait_for_completion(task_id)
            print(f"Task {task_id} complete!")
        
        print("\nAll tasks completed!")
        
    finally:
        await client.close()


def sync_example():
    """Synchronous example using requests"""
    import requests
    
    url = "http://localhost:8000"
    
    # Upload APK
    with open("./test.apk", "rb") as f:
        files = {"file": ("test.apk", f, "application/vnd.android.package-archive")}
        data = {
            "so_download_url": "http://example.com/libranger-jni.so",
            "so_architecture": "arm64-v8a",
            "pkg_name": "com.example.app"
        }
        
        response = requests.post(f"{url}/upload", files=files, data=data)
        result = response.json()
    
    task_id = result["task_id"]
    print(f"Task ID: {task_id}")
    
    # Poll for status
    while True:
        response = requests.get(f"{url}/task_status/{task_id}")
        status = response.json()
        print(f"Status: {status['status']}")
        
        if status["status"] == "complete":
            print("Processing complete!")
            print(f"Download URL: {status['signed_apk_download_path']}")
            break
        elif status["status"] == "failed":
            print(f"Failed: {status['reason']}")
            break
        
        time.sleep(2)


if __name__ == "__main__":
    # Run async example
    asyncio.run(example_basic_usage())
    
    # Or run sync example
    # sync_example()

