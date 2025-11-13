[中文版](./ZH_README.md) | [Chinese Version](./ZH_README.md)

The `/data/app` directory stores APK resources after installation and requires root access,  
which typical Shizuku permissions (like MT Manager) cannot access.

The `run-as pkg` command only grants runtime permissions for a specific APK, providing access to static resource storage, not full control over the installation package itself.

This approach doesn't replace middleware within Android or at the system level.  
Instead, it extracts the APK from the device, unpacks it, directly replaces target files (e.g., dex/so/assets),  
then repacks, signs, and flashes it back to the device for verification.

Takes ~1 minute, no source code access required, no developer assistance needed, eliminates rebuild time.  
Ideal for quick verification of middleware or native library minor changes, flag toggles, or temporary fixes.

Use Cases
- Quick verification of middleware patches or parameter changes during testing

Experiments: 
1. Even with root replacement of middleware, only the runtime directory is modified, while the extracted base.apk remains  
the original APK and won't dynamically change. Given operation complexity, this path was abandoned.

2. Direct bytecode replacement using HDX tools. Feasibility: Yes, but requires equal-length patches and re-signing,  
otherwise the system rejects installation or loads pre-update files. Not attempted; saw an English post about byte replacement—feasible but more complex, abandoned.

3. Runtime Hook (Frida/Xposed)—preferred for quick verification (not attempted):  
Pros: No APK modification, no re-signing, immediate behavior verification.  
Cons: Requires injection tool support, varies across Android versions/process protection policies, difficult to locate some native symbols or obfuscated methods. Good for quick validation.
    
4. Dynamic library loading (place .so in app private directory and dlopen)—requires app cooperation:  
Pros: No APK signature change, only runtime loading logic.  
Cons: Requires app-side toggle or compatibility logic, increases code complexity, may be restricted by SELinux/private directory permissions across Android versions.



### Server Setup

#### Prerequisites (One-time setup)

```bash
# Install required tools
apt install apktool -y
apt install apksigner -y
apt install zipalign -y
```

# Install Python dependencies
pip3 install -r requirements.txt
```

#### Generate Keystore (Optional - auto-generated if not exists)

```bash
keytool -genkey -v -keystore test_keystore.jks -alias testalias -keyalg RSA -keysize 2048 -validity 36500 -storepass testpass -keypass testpass -dname "CN=TestUser, OU=Test, O=TestOrg, L=TestCity, ST=TestState, C=US"

# Verify generation
keytool -list -v -keystore test_keystore.jks -storepass testpass
```

#### Start Server

```bash
python3 py_server_demo.py
# Server runs on http://0.0.0.0:8000
```

### API Endpoints

#### 1. Upload APK - `/upload` (POST)

Upload APK file with middleware replacement configuration.

**Parameters:**
- `file`: APK file (multipart/form-data)
- `so_download_url`: URL to download replacement SO file
- `so_architecture`: Target architecture (`arm64-v8a` or `armeabi-v7a`)
- `pkg_name`: Package name
- `md5`: (Optional) Pre-calculated APK MD5 for cache checking

**Response:**
```json
{
  "task_id": "uuid",
  "status": "pending",
  "message": "APK processing started"
}
```

**Cached Response (if MD5 exists):**
```json
{
  "task_id": "uuid",
  "status": "complete",
  "cached": true,
  "signed_apk_download_path": "/download_cached/{md5}",
  "message": "APK already processed, returning cached version"
}
```

#### 2. Check Task Status - `/task_status/{task_id}` (GET)

**Response:**
```json
{
  "task_id": "uuid",
  "status": "complete",
  "filename": "app.apk",
  "pkg_name": "com.example.app",
  "file_md5_before": "abc123...",
  "file_md5_after": "def456...",
  "so_md5_before": "old123...",
  "so_md5_after": "new456...",
  "so_architecture": "arm64-v8a",
  "real_so_architecture": "arm64-v8a",
  "start_process_timestamp": 1699999999.123,
  "end_process_timestamp": 1699999999.456,
  "total_consume_seconds": 45.23,
  "signed_apk_download_path": "/download/{task_id}"
}
```

**Failed Response:**
```json
{
  "task_id": "uuid",
  "status": "failed",
  "reason": "Architecture mismatch: requested arm64-v8a, but file is armeabi-v7a"
}
```

#### 3. Download Processed APK - `/download/{task_id}` (GET)

Downloads the signed APK file.

#### 4. Download Cached APK - `/download_cached/{md5}` (GET)

Downloads previously processed APK from cache.

### Processing Workflow

1. **Receive APK Upload** → Return `task_id`
2. **Check MD5 Cache** → If exists, return cached result immediately
3. **Validate MD5** → Confirm uploaded file MD5 matches request
4. **Create Work Path** → `pkg_name + md5` (if enabled) or `md5` only
5. **Extract APK** → Using `apktool d -r -s`
6. **Download SO File** → From `so_download_url`
7. **Verify Architecture** → Use `file` command to detect SO architecture
   - 64-bit → `arm64-v8a` (aarch64)
   - 32-bit → `armeabi-v7a` (arm)
   - Confirm matches requested architecture
   - Check MD5 differs from existing SO
8. **Replace Library** → Copy new SO to `extracted/lib/{architecture}/`
9. **Rebuild APK** → 
   - `apktool b` → unsigned.apk
   - `zipalign` → aligned.apk
   - `apksigner` → signed.apk
   - Delete intermediate APK files
10. **Update Index** → Store MD5 mapping for future cache hits

### Configuration

Edit `py_server_demo.py` to configure:

```python
ENABLE_PKGNAME_BASED_PATH = True  # Use pkg_name + md5 for path names
```
 
``` log

Verifies
Verified using v1 scheme (JAR signing): true
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
Verified using v4 scheme (APK Signature Scheme v4): false
Verified for SourceStamp: false
Number of signers: 1
WARNING: META-INF/services/io.grpc.NameResolverProvider not protected by signature. Unauthorized modifications to this JAR entry will not be detected. Delete or move the entry outside of META-INF/.
WARNING: META-INF/services/io.grpc.LoadBalancerProvider not protected by signature. Unauthorized modifications to this JAR entry will not be detected. Delete or move the entry outside of META-INF/.
WARNING: META-INF/services/kotlinx.coroutines.CoroutineExceptionHandler not protected by signature. Unauthorized modifications to this JAR entry will not be detected. Delete or move the entry outside of META-INF/.
WARNING: META-INF/services/kotlinx.coroutines.internal.MainDispatcherFactory not protected by signature. Unauthorized modifications to this JAR entry will not be detected. Delete or move the entry outside of META-INF/.
WARNING: META-INF/services/io.grpc.ManagedChannelProvider not protected by signature. Unauthorized modifications to this JAR entry will not be detected. Delete or move the entry outside of META-INF/.
```


### Client
```

1.
adb shell pm path your.package.name  # Get installation path
2. pull and output local path 
3. upload to server
4. wait for signal to download, download
5. install then output log
6. if "signatures" in real_time_err_log then uninstall && install 
err_log example
```

``` log
Performing Streamed Install
adb: failed to install D:\Download\new\new_aligned_signed.apk: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package com.mobile.brasiltvmobile signatures do not match previously installed version; ignoring!]
```


Print md5, file size, duration comparison


TIP
```
1. Hardened/packed APKs likely won't work
2. Except specific APKs (none yet), middleware replacement means signature change—requires uninstall then install, data will be lost
3. Takes ~1 minute, please wait during execution
```



### Alternative Test
Using zip approach
```

1.
unzip base.apk -d extracted_zip

After replacement

2.
zip -r -9 zip_unsigned.apk . -x "META-INF/*" -0 "resources.arsc" -0 "AndroidManifest.xml"
Remaining steps identical

resources.arsc parameter due to error below
Performing Streamed Install
adb: failed to install D:\Download\tmp\base\base aligned_signed.apk: Failure [-124: Failed parse during installPackageLI: Targeting R+ (version 30 and above) requires the resources.arsc of installed APKs to be stored uncompressed and aligned on a 4-byte boundary]


```

Conclusion: 
- 1. zip approach works, but package size triples even with max compression
- 2. Cannot combine with apktool—missing necessary files; sticking with most stable approach
- 3. META-INF is original signature directory, requires manual handling; apktool handles automatically during packing (speculation, as package size decreased)




### Notes


zipalign is a Google-required alignment step.  
apksigner signature is the first layer Android system validates; installation will fail without any signature.

test_keystore contains private and public keys. Since many APKs are handled, each uses different keys—no processing for now. May later assign specific commonly-used apps to use developer-provided keystore to avoid signature issues.

Self-signing vs. original signature: 
- If APK doesn't validate signatures, self-signing works fine; just conflicts with original package requiring reinstall
- Hardened packages may have built-in signature validation (but if unpacking succeeds, does this function still work?)

Obfuscation:
Code is usually obfuscated to prevent full decompilation, but traces remain findable. Assuming obfuscation is present,  
decompiled output isn't true source, but LLMs understand the concepts and can grasp general meaning.


