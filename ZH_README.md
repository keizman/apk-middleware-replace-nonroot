/data/app 目录是一个 APK 安装后的内置资源存储目录，访问权限需要 root，  
而通常的 shizuku 赋权（像 MT Manager 这类工具）依旧无法访问。  

run-as pkg 命令也只是获取了指定 APK 的运行时权限，获得的也只是常用的静态资源存储内容, 并非对安装包本身的完全控制。

接下来介绍的方式并非在 Android 内部进行中间件替换或替换系统层的中间件组件，  
而是将 APK 从设备上提取出来进行解包（解压）后直接替换目标文件（例如 dex / so / assets），  
之后重新打包并签名，再回刷到设备上进行验证。

此操作耗时大约 1m 左右，且无需获取源码权限、无需开发人员协助，减少重新 build 的耗时，  
适用于快速验证中间件或 native 库小范围修改、开关逻辑、修复临时问题的场景。

适用场景
- 测试过程中需要快速验证 middleware 补丁或参数变更；

实验: 
1.即使使用 root 手机替换了middleware, 其也只是替换的运行目录, 而提取的安装包 base.apk 是
原始 APK, 其并不会动态更改. ——最后考虑到操作可能也较为复杂不考虑此路径

2.直接使用 HDX 等工具进行类似字节码替换. AI 分析可行性为:    可以，但前提是只做等长的小补丁且需要重新签名, 否则系统会拒绝安装或加载更新前的文件。  ——此项未尝试, 之前有看到一篇英文帖子尝试了字节替换, 可行但复杂度更高, 不考虑

3.运行时 Hook（Frida / Xposed）——快速验证首选（未尝试）：  
优点: 无需改 APK、无需重签、能立即验证行为；
缺点：需要注入工具支持、不同 Android 版本/进程保护策略有差异，对某些 native 符号或混淆后的方法不易定位。适合作为快速验证手段。
    
4.动态加载新库（把 .so 放到应用私有目录并 dlopen）——需要应用配合：  
优点：不改 APK 签名，只改运行时加载逻辑；
缺点：需要在应用里加开关或兼容逻辑，增加代码复杂度，并且在不同 Android 版本的 SELinux / 私有目录权限下可能受限。



### 服务器设置

#### 前置条件 (一次性设置)

```bash
# 安装必需工具
apt install apktool -y
apt install apksigner -y
apt install zipalign -y
apt install python3 python3-pip -y

# 安装 Python 依赖
pip3 install -r requirements.txt
```

#### 生成密钥库 (可选 - 不存在时自动生成)

```bash
keytool -genkey -v -keystore test_keystore.jks -alias testalias -keyalg RSA -keysize 2048 -validity 36500 -storepass testpass -keypass testpass -dname "CN=TestUser, OU=Test, O=TestOrg, L=TestCity, ST=TestState, C=US"

# 验证生成有效
keytool -list -v -keystore test_keystore.jks -storepass testpass
```

#### 启动服务器

```bash
python3 py_server_demo.py
# 服务器运行在 http://0.0.0.0:8000
```

### API 接口

#### 1. 上传 APK - `/upload` (POST)

上传 APK 文件并配置中间件替换。

**参数:**
- `file`: APK 文件 (multipart/form-data)
- `so_download_url`: 下载替换 SO 文件的 URL
- `so_architecture`: 目标架构 (`arm64-v8a` 或 `armeabi-v7a`)
- `pkg_name`: 包名
- `md5`: (可选) 预先计算的 APK MD5 用于缓存检查

**响应:**
```json
{
  "task_id": "uuid",
  "status": "pending",
  "message": "APK processing started"
}
```

**缓存响应 (如果 MD5 存在):**
```json
{
  "task_id": "uuid",
  "status": "complete",
  "cached": true,
  "signed_apk_download_path": "/download_cached/{md5}",
  "message": "APK already processed, returning cached version"
}
```

#### 2. 检查任务状态 - `/task_status/{task_id}` (GET)

**响应:**
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

**失败响应:**
```json
{
  "task_id": "uuid",
  "status": "failed",
  "reason": "Architecture mismatch: requested arm64-v8a, but file is armeabi-v7a"
}
```

#### 3. 下载处理后的 APK - `/download/{task_id}` (GET)

下载已签名的 APK 文件。

#### 4. 下载缓存的 APK - `/download_cached/{md5}` (GET)

下载之前处理过的缓存 APK。

### 处理流程

1. **接收 APK 上传** → 返回 `task_id`
2. **检查 MD5 缓存** → 如果存在,立即返回缓存结果
3. **验证 MD5** → 确认上传文件 MD5 与请求匹配
4. **创建工作路径** → `pkg_name + md5` (如果启用) 或仅 `md5`
5. **解压 APK** → 使用 `apktool d -r -s`
6. **下载 SO 文件** → 从 `so_download_url`
7. **验证架构** → 使用 `file` 命令检测 SO 架构
   - 64位 → `arm64-v8a` (aarch64)
   - 32位 → `armeabi-v7a` (arm)
   - 确认与请求的架构匹配
   - 检查 MD5 与现有 SO 不同
8. **替换库文件** → 复制新 SO 到 `extracted/lib/{architecture}/`
9. **重新打包 APK** → 
   - `apktool b` → unsigned.apk
   - `zipalign` → aligned.apk
   - `apksigner` → signed.apk
   - 删除中间 APK 文件
10. **更新索引** → 存储 MD5 映射以便将来缓存命中

### 配置

编辑 `py_server_demo.py` 进行配置:

```python
ENABLE_PKGNAME_BASED_PATH = True  # 使用 pkg_name + md5 作为路径名
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


### client
```

1.
adb shell pm path your.package.name 获取安装路径
2.pull and ouput local path 
3.upload to server
4.wait for signal to downlaod, download
5.install . then output log
6.if "signatures" in real_time_err_log then uninstall && install 
err_log example
```

``` log
Performing Streamed Install
adb: failed to install D:\Download\new\new_aligned_signed.apk: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package com.mobile.brasiltvmobile signatures do not match previously installed version; ignoring!]
```


打印 md5 file size 耗时 对比


TIP
```
1.加固包大概率不可用
2.除特定 apk: (暂无) 外, 替换中间件意味着签名改变需要 uninstall 再安装, 数据会丢失
3.耗时大约 1 分钟左右, 运行期间请等待
```



### Anthor test
使用 zip  测
```

1.
unzip base.apk -d extracted_zip

替换后

2.
zip -r -9 zip_unsigned.apk . -x "META-INF/*" -0 "resources.arsc" -0 "AndroidManifest.xml"
其它步骤相同

resources.arsc  参数因为下方报错
Performing Streamed Install
adb: failed to install D:\Download\tmp\base\base aligned_signed.apk: Failure [-124: Failed parse during installPackageLI: Targeting R+ (version 30 and above) requires the resources.arsc of installed APKs to be stored uncompressed and aligned on a 4-byte boundary]


```

结论为: 
- 1.zip 方式也可以, 但打包后的目录文件 3 倍大, 即使使用最高压缩. 
- 2.且与 apktool 不能组合 缺少必要文件, 目前还是决定使用最稳妥方式
- 3.META-INF 是原签名目录, 这里需要手动处理, apktool 打包时会自动处理(此为推测, 因为包变小了)




### Record


zipalign 是 google 要求的必须要对其的步骤, 
apksigner 签名是第一层 android 系统就会校验, 不进行任何签名安装会也会报错, 

test_keystore 内装的就是密钥和公钥内容, 目前因需要操作的 APK 较多, 每一个使用的密钥都不同, 因此先不做处理, 之后会考虑给一个特定的常用 APP 指定使用开发者提供的 keystore 以避免签名问题

自签与使用原始签名区别: 
- 若 APK 本身没有校验签名使用自签方式没问题, 只是会与原始包冲突需要重装
- 加固包可能自带了签名校验功能, (可是假设去壳成功了这部分功能还有用吗)

混淆:
代码一般会进行混淆, 放置反编译后完全查看到代码, 但是若要找东西依旧有迹可循, 因此假设加了混淆
因此一般反编译出的根本完全不是 source, 只是 LLM 有相关概念, 能看懂大概意思




----

设计完善 prompt

1.server 和 client 支持多线程传输, 内网速度够快, 先不考虑

enable_pkgName_based_path = Ture or false 

每一步此任务处于不同状态
1.接收APK , 完成后服务器返回 task_id
3.check MD5 is request.md5 then md5sum_res = request.md5 , 
4.create path name f_path = pkg_name + md5sum_res if enable_pkgName_based_path else md5sum_res
5.extract use apktool
6.download from  so_download_url as {A} file 
7.confirm the real_so_architecture and the so_architecture are equal
real_so_architecture = file {A}
if 64-bit is arm64-v8a
if 32-bit is armeabi-v7a
then 
B = extracted/lib/{real_so_architecture}
check md5sum(A) and md5sum(B) are different

8.Replacing lib, mv A to B
9.Rebuilding APK, Aligning APK, Signing APK
keep the final apk (Signing APK) delete other .apk file, no need delete origin apk file and extracted folder

10.记录 md5 到 index

success status: complete, and other param 
generate respone {task_id: , "filename": file.filename, "file_md5_before": "",  "file_md5_after": "", "so_md5_before": "",  "so_md5_after": "so_architecture","real_so_architecture", total_consum...: , start_process_timestamp: , end_process_timestamp, Signed_apk_download_path:"", pkg_name: ""}

mission failed status:  failed, reason....

arm64-v8a or is equal aarch64, 
armeabi-v7a

branch 1
1.IF request.md5 in index file, Then excute form step 6, and no need receive more bytes from client




API 
/upload input {"filename": file.filename, "md5": md5sum(save_path), "so_download_url", so_architecture: only suport arm64-v8a or  armeabi-v7a, pkg_name: ""}

/task_status API check the current status

so_download_url defualt


完全推翻之前的代逻辑重写, 你可考虑更加完善的处理, 比如 index 只储存 一个 MD5 判断是否存在足够好? or 使用更好更快的结构体, 后期我会再考虑定期删除的, 暂定不会有太多的 index 内容 百条足矣, 你可类推. 
不使用 process_apk.sh, 而是函数指定不同的命令, 提高自由度
client 你可以再最后给一个 example 即可包括框架定义, 不用太详细,  因为不会实际在这里使用 client 