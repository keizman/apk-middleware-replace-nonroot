#!/usr/bin/env python3
"""
Simple API test script to verify server functionality
"""

import requests
import sys

def test_server_connection():
    """Test if server is running"""
    print("Testing server connection...")
    try:
        response = requests.get("http://localhost:8000/", timeout=5)
        if response.status_code == 200:
            print("✓ Server is running")
            print(f"  Response: {response.json()}")
            return True
        else:
            print(f"✗ Server returned status code: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("✗ Cannot connect to server")
        print("  Make sure the server is running: python3 py_server_demo.py")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def test_index_endpoint():
    """Test index endpoint"""
    print("\nTesting index endpoint...")
    try:
        response = requests.get("http://localhost:8000/index", timeout=5)
        if response.status_code == 200:
            index = response.json()
            print(f"✓ Index endpoint working")
            print(f"  Cached entries: {len(index)}")
            if index:
                print(f"  Sample: {list(index.keys())[0][:16]}...")
            return True
        else:
            print(f"✗ Index endpoint returned: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def test_upload_validation():
    """Test upload endpoint validation"""
    print("\nTesting upload validation...")
    try:
        # Test without required parameters
        response = requests.post(
            "http://localhost:8000/upload",
            data={
                "pkg_name": "test.app",
                "so_architecture": "invalid_arch"
            },
            timeout=5
        )
        # Should fail with 422 (validation error) or 400
        if response.status_code in [400, 422]:
            print("✓ Upload validation working")
            return True
        else:
            print(f"✗ Expected validation error, got: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def test_task_status_not_found():
    """Test task status with invalid ID"""
    print("\nTesting task status endpoint...")
    try:
        response = requests.get(
            "http://localhost:8000/task_status/invalid-task-id",
            timeout=5
        )
        if response.status_code == 404:
            print("✓ Task status endpoint working (404 for invalid ID)")
            return True
        else:
            print(f"✗ Expected 404, got: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def main():
    print("=" * 50)
    print("APK Middleware Server API Test")
    print("=" * 50)
    
    tests = [
        test_server_connection,
        test_index_endpoint,
        test_upload_validation,
        test_task_status_not_found,
    ]
    
    results = []
    for test in tests:
        result = test()
        results.append(result)
    
    print("\n" + "=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("✓ All tests passed!")
        sys.exit(0)
    else:
        print("✗ Some tests failed")
        sys.exit(1)

if __name__ == "__main__":
    main()

