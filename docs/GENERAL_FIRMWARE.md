# General firmware workflow / 通用固件流程

QEMU Everything keeps three separate facts visible: what was detected in a file,
which startup method has been configured, and what has actually been verified.
Importing a package does not establish that its original system can boot.

QEMU Everything 分别记录文件分析结果、配置的启动方法及实际验证情况。
成功导入升级包，不代表其原始系统已能启动。

## Choose a route / 选择启动路线

| Input / 输入 | Route / 路线 | What still needs verification / 待验证内容 |
| --- | --- | --- |
| Known Hasselblad `.cim` | Original UI adapter / 原厂 UI 适配器 | UI capabilities in the existing verification matrix / 原有验证矩阵中的 UI 功能 |
| Kernel built for a QEMU board | Direct kernel boot / 直接启动内核 | Console, initrd, root filesystem and drivers / 控制台、initrd、根文件系统及驱动 |
| Bootable disk image | QEMU disk boot / 磁盘启动 | Board, firmware and disk controller compatibility / 平台、引导固件及磁盘控制器兼容性 |
| Proprietary or encrypted update | Inspection and support workspace / 分析及支持工作区 | Container extraction, boot chain, hardware model and drivers / 解包、启动链、硬件模型及驱动 |

Recognized Sony `.DAT` packages can now be decrypted and extracted offline;
actual A7 IV 2.00 and A7R V 4.00 samples pass the container checks. The project does not
include a verified Sony camera board or original Sony UI adapter. The Hasselblad
adapter runs extracted original UI programs in a development guest; it does not
boot the camera's original kernel.

已识别的索尼 `.DAT` 可离线解密与提取，真实 A7 IV 2.00 和 A7R V 4.00 样本已通过校验；
项目尚无已验证的索尼相机硬件或原厂 UI 适配器。
哈苏路线是在开发虚拟机中运行提取的原厂 UI 程序，没有启动相机原厂内核。

For checked camera components and the separately installed Nikon Z7 assisted UI
lab, see [Camera firmware research](CAMERA_FIRMWARE.md).
相机组件解包与单独配置的尼康 Z7 辅助启动实验见[多品牌固件记录](CAMERA_FIRMWARE.md)。

## Import and investigate / 导入与分析

Drop a local firmware file into the library, or use the CLI after a source
installation. The source file is read without modification. New support files
belong to the imported library entry.

将本地固件拖入固件库，或在源码安装后使用命令行。原文件只读；新建支持文件
归属导入后的固件库条目。

```powershell
qemu-everything inspect C:\Firmware\update.dat
qemu-everything import C:\Firmware\update.dat
qemu-everything boards
```

`inspect` prints JSON. `import` prints the local device directory. `boards` lists
the available boot-profile templates. These are QEMU board choices, rather than
a list of verified proprietary firmware.

`inspect` 输出 JSON，`import` 输出本地固件目录，`boards` 列出启动配置模板。
平台模板对应 QEMU 虚拟主板，不能作为专有固件已验证启动的清单。

## Review the boot profile / 审阅启动配置

The support page lets you inspect and edit `boot.json`. A profile selects a board
and memory allocation, then names the kernel, optional initrd and/or disk image.
Available board IDs are `arm64-virt`, `arm-virt`, `x86_64-pc` and `riscv64-virt`.
Disk-only startup currently uses `x86_64-pc`; the `virt` profiles require a direct
kernel. ARM/RISC-V disk-only boot needs a separate firmware adapter.

支持页可以查看、编辑 `boot.json`。配置选择主板和内存，再指定内核、可选的
initrd 或磁盘镜像。当前平台 ID 为 `arm64-virt`、`arm-virt`、`x86_64-pc`
及 `riscv64-virt`。
当前纯磁盘启动使用 `x86_64-pc`；`virt` 平台需要直接提供内核。
ARM / RISC-V 纯磁盘启动需要另外的引导固件适配器。

Use the support page's **Add kernel**, **Add initrd** or **Add disk** buttons to
copy components into the selected library entry, then choose **Validate & save**
before starting. These actions retain the original files and update the local
profile. Generic output opens in a separate QEMU window.

在支持页使用 **添加内核**、**添加 initrd** 或 **添加磁盘** 按钮，将组件复制到
当前固件库条目，再点击 **验证并保存配置**。原始组件文件保留，修改的是本地启动
配置；通用虚拟机的画面显示在独立 QEMU 窗口。

| Field / 字段 | Meaning / 含义 |
| --- | --- |
| `schema` | Profile schema version, currently `1` / 配置版本，当前为 `1` |
| `board` | A supported QEMU board ID / 可用的 QEMU 平台 ID |
| `memory_mib` | Guest RAM in MiB / 虚拟机内存，单位 MiB |
| `cpu` | CPU model valid for that profile / 与平台匹配的 CPU 型号 |
| `kernel` | Relative path to a compatible kernel / 兼容内核的相对路径 |
| `initrd` | Optional relative path to an initial ramdisk / 可选 initrd 相对路径 |
| `disk` | Optional relative path to a guest disk image / 可选磁盘镜像相对路径 |
| `disk_format` | Explicit image format, such as `raw` or `qcow2` / 明确的镜像格式 |
| `cmdline` | Guest kernel command line / 虚拟机内核命令行 |
| `status` | Support record, initially a draft / 支持记录，初始为草稿 |
| `network` | Must be `false`; generic networking is disabled in this version / 必须为 `false`；此版本关闭通用虚拟机网络 |

For example, an ARM64 Linux Image built for QEMU `virt` can start with this
profile. A usable system still needs its matching initrd or root disk; this
example only selects the kernel and serial console.

例如，为 QEMU `virt` 构建的 ARM64 Linux Image 可以使用以下配置。
完整系统还需匹配的 initrd 或根磁盘；这个示例只选择内核和串口控制台。

```json
{
  "schema": 1,
  "board": "arm64-virt",
  "memory_mib": 1024,
  "cpu": "cortex-a57",
  "kernel": "source/firmware.bin",
  "initrd": "",
  "disk": "",
  "disk_format": "raw",
  "cmdline": "console=ttyAMA0,115200",
  "status": "draft",
  "network": false
}
```

Component paths must stay inside the imported device directory. Put separately
obtained kernels, initrds and disk images in that directory before selecting them.
Changing a suffix or selecting a CPU model cannot convert a proprietary firmware
into a QEMU-compatible image. Use a kernel built for the selected board and provide
the root filesystem it expects.

组件路径必须位于导入后的固件目录内。额外取得的内核、initrd 或磁盘镜像应先
放入该目录，再写入相对路径。修改扩展名或选择 CPU 型号不会让专有固件自动兼容
QEMU；内核需要为对应平台构建，并提供其预期的根文件系统。

Validation checks the configuration and local inputs. It does not prove a guest
will finish booting. Support records should distinguish “QEMU process started”,
“kernel reached console”, “userspace started” and “UI tested”. Record logs or
screenshots for the milestone you actually observed.

配置校验检查参数及本地输入，无法证明系统会完成启动。支持记录应区分“QEMU
进程启动”“内核进入控制台”“用户空间启动”“UI 已实测”，并保留对应日志或截图。

## Run / 启动

Generic firmware uses QEMU independently of the Android camera UI runtime:

通用固件单独使用 QEMU，无需 Android 相机 UI 运行时：

```powershell
qemu-everything setup
qemu-everything run <device-directory>
qemu-everything run <device-directory> --headless
```

The GUI can launch the same reviewed profile. Generic guests use QEMU's native
display window; serial output appears in the workbench console. Their displays
do not use the embedded camera touch/frame transport. Disk writes use QEMU's
temporary snapshot mode, and generic networking is disabled. Stop closes the
owned QEMU process; the CLI also stops it on Ctrl+C.

GUI 可以启动相同的配置。通用虚拟机使用 QEMU 独立显示窗口，串口输出进入
工作台控制台；不使用相机专用的内嵌触控和画面传输。磁盘写入采用 QEMU 临时
快照，通用启动关闭网络。停止按钮关闭本次 QEMU 进程，CLI 也支持 Ctrl+C 退出。

For Hasselblad original UI sessions, use the GUI and its existing Android SDK
license flow. The CLI requires explicit acceptance of the [Android SDK
terms](https://developer.android.com/studio/terms) before a new camera SDK download:

哈苏原厂 UI 使用 GUI 及已有 Android SDK 许可流程。CLI 首次下载相机 SDK 前，
需阅读 [Android SDK 许可](https://developer.android.com/studio/terms)并显式接受：

```powershell
qemu-everything setup --camera --accept-android-sdk-license
```

An already installed camera runtime can be reused with `setup --camera` without
the acceptance flag. The flag applies to a new SDK download; it is not needed for
QEMU-only setup.

运行时已安装时，可直接使用 `setup --camera`，无需重复传入许可参数。
该参数用于新的 SDK 下载；仅准备 QEMU 无需此参数。

## Build support evidence / 建立支持记录

```powershell
qemu-everything support <device-directory>
python -m devkit --home C:\QEMU-Library support <device-directory>
```

The support workspace contains `support/report.md` and
`support/diagnostics.json`, plus an editable `support/adapter-plan.json`. Review the detected format, architecture and blockers,
then use the workspace to keep your investigation and boot configuration together.
The GUI can export a support ZIP containing metadata and available logs; firmware
payloads are excluded. Inspect a support archive before sharing it, since guest
logs can contain text entered during development.

支持工作区包含 `support/report.md`、`support/diagnostics.json` 及可编辑的
`support/adapter-plan.json`。检查格式、
架构及阻塞项，在工作区统一保存调查结果和启动配置。GUI 可以导出元数据和
已有日志组成的支持 ZIP，不包含固件载荷；分享前检查内容，开发日志可能包含
你在虚拟机内输入的文字。

With the CLI, global `--home` appears before the subcommand. The newer
`QEMU_EVERYTHING_HOME` environment variable takes priority over the compatible
`X2DII_DEVKIT_HOME` alias. Existing data directories are reused.

CLI 的全局 `--home` 放在子命令前。新的 `QEMU_EVERYTHING_HOME` 环境变量优先
于兼容保留的 `X2DII_DEVKIT_HOME`，已有数据目录可继续使用。

## Extend support / 扩展支持

New platform support should provide reproducible detection, a documented boot
route, the required QEMU hardware or userspace adapter, and a verification record.
Automatic identification creates a starting point for this work. It cannot supply
missing proprietary drivers or decrypt arbitrary signed/encrypted update packages.

Installed Python packages may register adapters in the `qemu_everything.adapters`
entry-point group. An adapter exposes `id`, `label`, `matches(inspection)` and
`import_firmware(source, library, task) -> Path`. The built-in camera adapter is
retained independently. Firmware input files never supply executable host plugins.

新增平台应包含可复现的识别方法、明确的启动路线、所需 QEMU 硬件或用户空间
适配器及验证记录。自动识别为这些工作建立起点；缺失的专有驱动及任意签名、
加密升级包仍需分别研究。

已安装的 Python 包可以通过 `qemu_everything.adapters` entry-point 注册适配器，
提供 `id`、`label`、`matches(inspection)` 及
`import_firmware(source, library, task) -> Path` 接口。内置相机适配器继续保留；
固件输入文件不会提供可执行的主机插件。
