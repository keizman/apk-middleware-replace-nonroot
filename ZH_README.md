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



```

接收 apk 上传, 
1.校验缓存, 有则使用原有映射(已经执行过)

A
apktool  d -r -s base.apk -o  extracted

B:
wget http://10.8.16.141:8090/tmp/***.so

C:
cp ***.so extracted/lib/arm64-v8a/

D:
apktool  b extracted -o new_unsigned.apk

E:
zipalign -f -v 4 new_unsigned.apk new_aligned.apk
zipalign -c -v 4 new_aligned.apk


F:
apksigner sign --ks test_keystore.jks --ks-key-alias testalias --ks-pass pass:testpass --key-pass pass:testpass --in new_aligned.apk --out signed.apk
验证包有效
apksigner verify --verbose new_aligned.apk


response

替换前 ***.so md5, 替换后 md5, apk md5 替换前后, 
 
 
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


#### 在服务器启用 SMB, 之后直接使用 adb install smbpath 即可安装, 无需下载, 无需想 webdav 必须 mapping, 只要可访问此服务器即可一键安装

```
wget https://github.com/9001/copyparty/releases/latest/download/copyparty-sfx.py
python -m pip install impacket

python copyparty-sfx.py --smb -v .::r -a uname:passwd   ---安全环境可以不输入 -a 设置密码, r 权限已只读

adb install \\ip\a\signed.apk
```

找不到路径可以再 Windows 开启 映射输入 IP 后 浏览其会显示所有 path, 找到对应的即可



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

-----

写一个 英文接口文档 表名接收参数范围
增加一个设计, 客户端会再上传前计算 md5 后请求 index 接口, 如果md5 不再其中 正常处理, 如果再其中则使用选择新加的接口, exist_pkg, 传入的parameter 与 /upload 接口相同, 但不再需要上传文件, 因为有缓存, 这样分开两个接口的设计我觉得更好一些, 

为了保密和高灵活性, 现决定将 downloaded_so existing_so 等最后放的命名方式, 全部变为客户端参数传入, 传入parameter 为dict - so_name: "download url " 方式, 有几个就替换几个, 比如如果 不同 so_name 并且其确实存在于 lib 即可执行替换, 另外务必检查所有 下载后的文件与 传入的 so 架构相符, 任何一个不符则代表此次任务失败. 