# Fujifilm instax mini Evo 固件检查

项目支持检查 SUNP BURN 格式，并解包已验证的 instax mini Evo 1.09 官方更新。其他 SUNP 样本可识别容器类型，解包限于下列已验证样本。固件包由用户自行提供。[富士官方固件页面](https://www.instax.com/mini_evo/en/support/firmware/)

| 已验证样本 | 值 |
| --- | --- |
| 文件 | FPUPDATE.DAT |
| 大小 | 97,328,468 字节 |
| SHA-256 | 4f412d08688443212c2cf350586a8c01d43d467c580eb19389a82b40929a93ed |
| 架构 | MIPS32 小端，ThreadX |

## 离线检查和解包

在仓库根目录执行，输出路径请指向一个新目录。

~~~powershell
python -m devkit.cli inspect 'C:\Firmware\FPUPDATE.DAT'
python -m devkit.cli unpack 'C:\Firmware\FPUPDATE.DAT' --output '.local\instax-mini-evo-1.09'
~~~

解包器验证文件大小和 SHA-256，检查容器区间、两层加法校验和，并使用已核对的内嵌公钥离线验证 RSA-2048/SHA-256 签名。结果包括升级程序、字节保持一致的 MIPS 主程序、两个 FAT16 资源卷、供静态分析使用的 ELF 包装，以及记录布局和验证结果的 sunp-layout.json。整个流程读取原始固件文件并将产物写入指定本地目录。相机启动和真机签名验证行为仍待验证。

## 连接 instax mini Evo 2 现有 QEMU 会话

mini Evo 2 的 Qt 查看器可连接已经运行的系统 QEMU 客体。将现有控制器输出目录传给工作台。

~~~powershell
python run_app.py --instax-native-run 'C:\QEMU\instax-run'
~~~

查看器读取控制器记录的画面、状态和启动日志，并把触摸事件送入客体输入队列。关闭查看器后，原有 QEMU 会话继续运行。mini Evo 1.09 的 SUNP 解包输出仅用于离线分析。
