# APK Middleware Replacement Without Root

The `/data/app` directory is a built-in resource storage directory after APK installation, requiring root access.  
Even common Shizuku permissions (like tools such as MT Manager) still cannot access it.  

The `run-as pkg` command only obtains runtime permissions for the specified APK, gaining access only to common static resource storage content, not complete control over the installation package itself.

The method introduced here is not about middleware replacement within Android or replacing system-level middleware components,  
but rather extracting the APK from the device, decompressing it, directly replacing target files (such as dex / so / assets),  
then repackaging and signing it, and flashing it back to the device for verification.

This operation takes approximately 1 minute, requires no source code access, no developer assistance, reduces rebuild time,  
and is suitable for quickly verifying middleware or native library small-scale modifications, switch logic, and temporary issue fixes.

## Use Cases
- Quick verification of middleware patches or parameter changes during testing

## Experiments: 
1. Even if middleware is replaced using a rooted phone, it only replaces the runtime directory, while the extracted installation package base.apk remains the original APK and will not be dynamically changed. ——This path was not considered due to potentially complex operations

2. Direct bytecode replacement using tools like HDX. AI feasibility analysis: Possible, but requires equal-length small patches and re-signing, otherwise the system will refuse installation or load pre-updated files. ——Not attempted; previously saw an English post attempting byte replacement, feasible but more complex, not considered

3. Runtime Hook (Frida / Xposed) ——Preferred for quick verification (not attempted):  
Pros: No need to modify APK, no re-signing, immediate behavior verification;
Cons: Requires injection tool support, variations across different Android versions/process protection policies, difficulty locating some native symbols or obfuscated methods. Suitable as a quick verification method.
    
4. Dynamic loading of new libraries (place .so in application private directory and dlopen) ——Requires application cooperation:  
Pros: No APK signature modification, only runtime loading logic changes;
Cons: Requires adding switches or compatibility logic in the application, increases code complexity, and may be restricted under different Android versions' SELinux / private directory permissions.


## Server Setup
One-time installation:
```bash
apt install apktool -y
apt install apksigner -y
apt install zipalign -y
```

Generate keystore:
```bash
keytool -genkey -v -keystore test_keystore.jks -alias testalias -keyalg RSA -keysize 2048 -validity 36500 -storepass testpass -keypass testpass -dname "CN=TestUser, OU=Test, O=TestOrg, L=TestCity, ST=TestState, C=US"

# Verify keystore generation
keytool -list -v -keystore test_keystore.jks -storepass testpass
```

## Workflow

Receive APK upload:
1. Check cache, use existing mapping if already executed

Step A:
```bash
apktool d -r -s base.apk -o extracted
```

Step B:
```bash
wget http://10.8.16.141:8090/tmp/libranger-jni.so
```

Step C:
```bash
cp libranger-jni.so extracted/lib/arm64-v8a/
```

Step D:
```bash
apktool b extracted -o new_unsigned.apk
```

Step E:
```bash
zipalign -f -v 4 new_unsigned.apk new_aligned.apk
zipalign -c -v 4 new_aligned.apk
```

Step F:
```bash
apksigner sign --ks test_keystore.jks --ks-key-alias testalias --ks-pass pass:testpass --key-pass pass:testpass --in new_aligned.apk --out signed.apk

# Verify package validity
apksigner verify --verbose new_aligned.apk
```

### Response
Returns: MD5 before/after ranger replacement, APK MD5 before/after replacement

```log
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

## Client
```
1. adb shell pm path your.package.name  # Get installation path
2. pull and output local path 
3. upload to server
4. wait for signal to download, download
5. install, then output log
6. if "signatures" in real_time_err_log then uninstall && install 
```

Error log example:
```log
Performing Streamed Install
adb: failed to install D:\Download\new\new_aligned_signed.apk: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package com.mobile.brasiltvmobile signatures do not match previously installed version; ignoring!]
```

Print MD5, file size, execution time comparison

## TIPS
```
1. Hardened packages likely won't work
2. Except for specific APKs (none currently), replacing middleware means signature changes, requiring uninstall and reinstall, which will lose data
3. Process takes approximately 1 minute, please wait during operation
```

## Alternative Test
Testing with zip:
```bash
# Step 1
unzip base.apk -d extracted_zip

# After replacement

# Step 2
zip -r -9 zip_unsigned.apk . -x "META-INF/*" -0 "resources.arsc" -0 "AndroidManifest.xml"
# Other steps remain the same

# resources.arsc parameter needed due to following error:
# Performing Streamed Install
# adb: failed to install D:\Download\tmp\base\base aligned_signed.apk: Failure [-124: Failed parse during installPackageLI: Targeting R+ (version 30 and above) requires the resources.arsc of installed APKs to be stored uncompressed and aligned on a 4-byte boundary]
```

### Conclusion: 
- 1. Zip method also works, but package file is 3x larger even with maximum compression
- 2. Cannot combine with apktool due to missing necessary files; decided to use the most stable method
- 3. META-INF is the original signature directory, needs manual handling; apktool handles this automatically during packaging (speculation, as package becomes smaller)

## Record

zipalign is a required alignment step mandated by Google.  
apksigner signing is the first layer Android system validates; installing without any signature will also fail.

test_keystore contains the private and public keys. Currently, due to the large number of APKs being operated on, each using a different key, no processing is done for now. In the future, consideration will be given to specifying a particular commonly used APP to use a keystore provided by the developer to avoid signature issues.

Difference between self-signing and using original signature:
- If the APK itself doesn't verify signature, self-signing works fine, but will conflict with original package requiring reinstall
- Hardened packages may have built-in signature verification (but if de-shelling succeeds, is this functionality still useful?)

### Obfuscation:
Code is generally obfuscated to prevent full code viewing after decompilation, but traces can still be found when looking for things, so assuming obfuscation is added.  
Therefore, decompiled code is generally not the source at all, only that LLM has relevant concepts and can understand the general meaning.
