# Camera firmware research / 多品牌相机固件研究

Verified locally on **2026-10-11**. Results apply to the listed model/version,
not every product sold by a brand. Vendor firmware, recovered tables and emulator
binaries are separate local inputs; they are not shipped in this repository.

**2026-10-11 本地实测**。以下结果按具体机型、版本记录，不能外推至整个品牌。
厂商固件、恢复的表和定制模拟器是单独准备的本地输入，不随本仓库分发。

## Results / 当前结果

| Actual input / 真实输入 | Extraction / 解包 | Original UI / 原厂 UI | Remaining work / 剩余工作 |
| --- | --- | --- | --- |
| Hasselblad X System | Existing CIM adapter / 原有 CIM 适配器 | See [verification matrix](VERIFICATION.md) / 见原有矩阵 | Original kernel, sensor/ISP/peripherals / 原厂内核、传感器与外设 |
| Nikon Z7 C3.80 | All module and package CRCs pass / 模块、整包 CRC 通过 | **Original menu and DOWN navigation verified / 原厂菜单及向下导航已验证** | Assisted startup modifies guest RAM; natural power-on and full imaging hardware unverified / 辅助启动修改 guest RAM，自然开机及完整拍摄硬件未验证 |
| Nikon Z30 C1.20, Z50 C2.60, Z6 C3.80, Z7II C1.70, Z8 C3.11 | All five other packages pass; six packages total, 24 modules / 另五份包通过，共六包、24 模块 | Not locally boot verified / 未验证启动 | Separate model-specific emulator and firmware fingerprint / 各自的模拟器与固件指纹 |
| Sony A7 IV / ILCE-7M4 2.00 | CXD90057_k8 AES/FDAT checks pass; original kernel executes in QEMU / 解密及校验通过，QEMU 已执行原厂内核 | No kernel console or UI / 未进入内核控制台或 UI | Early store to `0xFE005010` enters an exception path; Sony board MMIO, IPC, storage and display / 早期写该地址后进入异常路径，缺板级 MMIO、IPC、存储与显示 |
| Sony A7R V / ILCE-7RM5 4.00 | Actual package decrypted and extracted / 真实包已解密提取 | Not booted / 尚未启动 | Matching SoC and services / 对应 SoC 与服务 |
| Ricoh GR III 2.10, IIIx 1.60, IV 1.11 | Container and decoded sums pass; structural ROMFS extraction / 输入、输出加法校验通过，按 ROMFS 结构提取 | Not booted / 尚未启动 | Milbeaut board, RTOS/Linux IPC, display / Milbeaut 板型、RTOS 与 Linux IPC、显示 |
| Insta360 X3 v1.1.4.1_build1 | Six components, MD5/CRC checked / 六组件，MD5/CRC 通过 | Not booted; `arm64-virt` trial produced no serial output / 尚未启动，通用板型试验无串口输出 | Ambarella H22 board, RTOS cooperation and display / Ambarella H22 板型、RTOS 协作与显示 |
| Canon EOS 50D 1.0.9 | FIR checksum and updater XOR decoding pass / FIR 校验与升级器 XOR 解码通过 | Not booted / 尚未启动 | Main payload remains encrypted; qemu-eos needs full ROM0/ROM1 / 主载荷仍加密，qemu-eos 需要完整 ROM0/ROM1 |

CRC/additive checks detect corruption. They do **not** authenticate a vendor
signature or authorize a modified update. A running QEMU process, exit code 0,
or a detected Linux magic is not evidence that the original system booted.

CRC 与加法校验可检测损坏，不代表验证了厂商签名或允许修改后升级。
QEMU 进程启动、退出码 0、发现 Linux magic 都不能作为原厂系统启动成功的证据。

The final test suite passed **275 tests**. Source GUI and Windows portable GUI
both displayed the Z7 menu; cancel/exit cleaned their owned guest and listener.
Input fingerprints and verified milestones are saved in
[the machine-readable verification record](camera-verification-20261011.json).

最终 **275 项测试通过**。源码 GUI 与 Windows 便携版均显示 Z7 菜单，取消或退出后
清理自己的虚拟机与监听。样本指纹和验证结果见上述 JSON 记录。

## Use the workbench / 工作台使用

1. Download an official package and select the firmware file inside its ZIP.
2. Drop it into the workbench, then choose **Platform support → Extract camera components**.
3. Review `extracted/extraction.json`: method, checks, notes, component boundaries,
   file hashes and unverified boot milestones.
4. Configure a compatible board, or use the separately configured **Nikon Z7 menu lab**.

1. 从厂商官网下载升级包，先取出 ZIP 内的固件文件。
2. 拖入工作台，打开 **平台适配 → 解包相机组件**。
3. 查看 `extracted/extraction.json` 中的方法、校验、组件边界、哈希和启动状态。
4. 配置匹配板型；符合下文条件的 Z7 可使用 **尼康 Z7 原厂菜单实验**。

```powershell
# Source checkout; installed qemu-everything has the same commands.
# 源码目录中使用；安装后的 qemu-everything 命令参数相同。
.\.venv\Scripts\python -m devkit.cli cameras
.\.venv\Scripts\python -m devkit.cli inspect C:\Firmware\BODYDATA.DAT
.\.venv\Scripts\python -m devkit.cli unpack C:\Firmware\BODYDATA.DAT --output C:\Lab\a7m4-unpacked
.\.venv\Scripts\python -m devkit.cli import C:\Firmware\BODYDATA.DAT
.\.venv\Scripts\python -m devkit.cli unpack-device <device-directory>
.\.venv\Scripts\python -m devkit.cli support <device-directory>
```

The output directory must be new. Extraction is transactional, bounded and
cancellable; partial files are removed on failure. Inputs are never executed on
the host. Archive paths, links, special files, counts and total output are checked.
Support exports contain the extraction record, **not the proprietary components**.
Refresh checks artifact presence and sizes; launch rechecks the exact Nikon payload
hash. The extraction record's hashes describe the bytes at extraction time.

输出目录必须不存在。解包有大小上限，可取消，失败会清理未完成文件；不在宿主执行
厂商程序。检查归档路径、链接、特殊文件、文件数与总输出。适配导出只包含解包记录，
不含厂商组件。刷新检查文件存在与大小；尼康启动再次核对真实载荷哈希。
解包记录中的哈希对应解包时的文件内容。

## Nikon Z7 lab / 尼康 Z7 实验

The local Windows installation has been configured with WSL **Debian**, a QEMU
10.2.4 custom build, and Z7 C3.80. This is an experimental menu development lab.
It is separate from the stock QEMU runtime downloaded by `setup`.

本机已配置 WSL **Debian**、定制 QEMU 10.2.4 和 Z7 C3.80。
这是实验性的菜单开发环境，独立于 `setup` 下载的标准 QEMU。

### Reproduce the inputs / 准备输入

- Download [Z7 C3.80 from Nikon](https://downloadcenter.nikonimglib.com/en/download/fw/558.html).
- Recover legacy XOR tables using the reviewed [nikon-fw-key-tool revision](https://github.com/WonderLantern/nikon-fw-key-tool/tree/33426e892afd329a3b366b73776bcf53b9ac4ee8).
  That separate GPL-3.0 tool requires NumPy and several suitable official firmware
  samples for full table coverage. A single Z30 sample was insufficient locally
  and was correctly rejected. Recovery must pass module and package CRC checks.
- Supply its JSON through `--key-file`, the GUI file picker, or
  `<data-home>/camera-keys/nikon-tables.json`.
- Build [z7-qemu at this revision](https://github.com/WonderLantern/z7-qemu/tree/97abf0b81cd3211f8374a38987a0beaf4549b90f)
  using its documented QEMU 10.2.4 setup. Preserve `.local/build-manifest.json`,
  source hashes and the binary hash generated by that setup.

从尼康下载 Z7 C3.80，使用上述独立工具从官方样本恢复旧格式 XOR 表；覆盖不足时
必须停止，恢复结果需通过模块和整包 CRC。通过 `--key-file`、GUI 文件选择器或默认
路径提供 JSON。按固定版本的 z7-qemu 文档构建模拟器并保留构建清单。

The key tool documents this sufficient recovery set; all four were included in
the six-package local validation. Keep the `.bin` files together in a private
directory, preserving their filenames. The Z7 package is the lab input, and Z30
was an additional check, rather than the only recovery input.

密钥工具公布的下列四份样本足以覆盖表；本地六包校验包含这四份。将 `.bin` 保留
原文件名放在同一个私有目录。Z7 是菜单实验输入，Z30 是额外校验样本。

| Recovery input / 恢复输入 | Official page / 官方页面 |
| --- | --- |
| `Z_8_0311.bin` — Z8 C3.11 | [Nikon Z8](https://downloadcenter.nikonimglib.com/en/download/fw/606.html) |
| `Z7_2_0170.bin` — Z7II C1.70 | [Nikon Z7II](https://downloadcenter.nikonimglib.com/en/download/fw/561.html) |
| `Z_6_0380.bin` — Z6 C3.80 | [Nikon Z6](https://downloadcenter.nikonimglib.com/en/download/fw/557.html) |
| `Z_50_0260.bin` — Z50 C2.60 | [Nikon Z50](https://downloadcenter.nikonimglib.com/en/download/fw/555.html) |

For example, after placing those files in `C:\Lab\nikon-firmware`:
例如，先把上述样本放入 `C:\Lab\nikon-firmware`，再运行：

```powershell
git clone --config core.autocrlf=false https://github.com/WonderLantern/nikon-fw-key-tool.git C:\Lab\nikon-fw-key-tool
git -C C:\Lab\nikon-fw-key-tool checkout --detach 33426e892afd329a3b366b73776bcf53b9ac4ee8
py -3 -m venv C:\Lab\nikon-fw-key-tool\.venv
C:\Lab\nikon-fw-key-tool\.venv\Scripts\python -m pip install numpy
C:\Lab\nikon-fw-key-tool\.venv\Scripts\python C:\Lab\nikon-fw-key-tool\extract_key.py --firmware-dir C:\Lab\nikon-firmware --out C:\Lab\nikon-tables.json
```

The following build example uses an existing WSL Debian distribution with
**Python 3.12 or newer**. Substitute your Linux user directory for `/home/user`.
Keep checkout line endings unchanged: the lab validates exact source bytes.
The menu-only bridge uses standard Python; NumPy/Pillow in the upstream
requirements are needed for its optional photo fixtures.

下例使用已安装的 WSL Debian，要求 **Python 3.12 或更新版本**；请将 `/home/user`
替换为实际 Linux 用户目录。保留源码原始换行符，桥接会验证逐字节指纹。
菜单桥接仅需标准 Python；上游 NumPy/Pillow 用于可选的照片输入夹具。

```powershell
git clone --config core.autocrlf=false https://github.com/WonderLantern/z7-qemu.git C:\Lab\z7-qemu
git -C C:\Lab\z7-qemu checkout --detach 97abf0b81cd3211f8374a38987a0beaf4549b90f
wsl -d Debian --exec python3 --version
wsl -d Debian --exec sudo apt-get update
wsl -d Debian --exec sudo apt-get install -y build-essential ninja-build pkg-config python3-venv libglib2.0-dev libpixman-1-dev zlib1g-dev libfdt-dev
wsl -d Debian --exec python3 /mnt/c/Lab/z7-qemu/tools/build.py --work-dir /home/user/z7-qemu-build --jobs 4
```

The builder downloads and verifies QEMU 10.2.4, then records the Linux binary path
in `C:\Lab\z7-qemu\.local\build-manifest.json`. Use that exact `binary` value below;
the build is specific to the chosen WSL distribution. The upstream Windows
examples name a different distribution; this workbench configuration explicitly
selects Debian. A cold build needs network access for its build dependencies.

构建脚本下载并校验 QEMU 10.2.4，在上述构建清单中记录 Linux 二进制路径。
将清单的 `binary` 原值填入下方配置。构建对应所选 WSL 发行版；上游示例的发行版名
与本例不同，本工作台明确选择 Debian。首次构建需要联网获取构建依赖。

Create `<data-home>/nikon-lab.json`, adjusting the absolute paths:
在数据目录创建配置，填入实际的绝对路径：

```json
{
  "distro": "Debian",
  "project": "C:\\Lab\\z7-qemu",
  "qemu": "/home/user/z7-qemu-build/build/qemu-system-arm",
  "revision": "97abf0b81cd3211f8374a38987a0beaf4549b90f"
}
```

The default data home is `%LOCALAPPDATA%\QEMU-Everything`, or the existing
`%LOCALAPPDATA%\X2DII-DevKit` directory when present. `--home` must precede the
subcommand. This explicit example uses `C:\Lab\QEMU-Everything`: place the JSON
there, import the Z7 file, and use the library directory printed by `import`.

默认数据目录为 `%LOCALAPPDATA%\QEMU-Everything`；已有旧数据目录时继续使用
`%LOCALAPPDATA%\X2DII-DevKit`。`--home` 放在子命令之前。下例明确使用
`C:\Lab\QEMU-Everything`，将 JSON 放在该目录，先导入 Z7，再使用导入输出的库路径。

```powershell
$z7Device = .\.venv\Scripts\python -m devkit.cli --home C:\Lab\QEMU-Everything import C:\Lab\nikon-firmware\Z_7_0380.bin
.\.venv\Scripts\python -m devkit.cli --home C:\Lab\QEMU-Everything unpack-device $z7Device --key-file C:\Lab\nikon-tables.json
.\.venv\Scripts\python -m devkit.cli --home C:\Lab\QEMU-Everything nikon-lab $z7Device
```

The GUI opens the owned loopback server after readiness. The CLI prints
`http://127.0.0.1:8787`; open it in your browser. **Cancel** in the workbench or
**Ctrl+C** in the CLI stops the owned server and guest. Port conflicts fail before
launch. Script, build and firmware fingerprints are checked before execution.

GUI 在自己的本地服务就绪后打开浏览器；CLI 输出固定地址。工作台点 **取消** 或
CLI 按 **Ctrl+C** 停止本次服务与虚拟机。端口占用时拒绝启动，执行前检查脚本、
构建与固件指纹。

Accepted MAIN: **33,052,088 bytes**, SHA-256
`e6df1764323fb1c287159dd3ce1c84ac93c72b96ba6eb09c070295c093fecbff`.
The Z7 extractor omits the module's two-byte CRC. Z30's upstream expects those
two bytes retained: do not substitute a Z30 module into this Z7 lab.

Accepted original update: `Z_7_0380.bin`, **34,472,614 bytes**, SHA-256
`07cf19513ada5b770caec4cfc3531a6734c48aede28dcec037f0f8374ef441a2`.
The reader checks all module CRCs and the package CRC, but does not authenticate
the keyed SHA-1 header digest. Newer Nikon formats are not handled by this reader;
processor generation alone does not identify the update format.

读包器验证各模块与整包 CRC，未认证文件头的带密钥 SHA-1 摘要。较新的尼康格式
不属于此解析器的支持范围；不能仅凭 EXPEED 代数判断升级包格式。

### What was observed / 实测内容

Original 720×480 **PLAYBACK MENU** rendered, and two DOWN inputs moved the
highlight from **Delete** to **Playback display options**. Firmware file hash
remained unchanged. The lab invokes original firmware functions and patches
guest state to reach the menu: **natural power-on is unverified**. This test did
not establish autofocus, sensor capture, genuine photos, or full camera hardware.

720×480 原厂回放菜单已显示，两次 DOWN 将选中项从 Delete 移到 Playback display
options，输入固件文件哈希未变。启动调用原厂函数并初始化、修改 guest 状态；
未验证自然开机、对焦、传感器拍摄、真实照片或完整相机硬件。

## Ricoh GR inputs / 理光 GR 输入

Use the `.bin` from each official ZIP. No external key file is needed for this
documented frame format. The exact local inputs were:

使用官方 ZIP 内的 `.bin`。此帧格式不需要独立密钥文件，实际验证输入如下：

| Version / 版本 | Package / 升级包 | Firmware file / 固件文件 | Input SHA-256 / 输入哈希 |
| --- | --- | --- | --- |
| [GR III 2.10](https://www.ricoh-imaging.co.jp/english/support/digital/gr3_s.html) | `gr3_v210.zip` | `fwdc239b.bin` | `d6a3c9c288d784080e7b283222ecdd8752adfeed6faa36dab44f73115354bfd4` |
| [GR IIIx 1.60](https://www.ricoh-imaging.co.jp/english/support/digital/gr3x_s.html) | `gr3x_v160.zip` | `fwdc243b.bin` | `708e79f3b7b96a7e1581b4acf4c412233050a1832718ef7f06c8397db1b431c7` |
| [GR IV 1.11](https://www.ricoh-imaging.co.jp/english/support/digital/gr4_s.html) | `gr4_v111.zip` | `fwdc248b.bin` | `a2f664dfca034059eb0fd6e18ab08684c326b4a034d85c164dad7e1ec9b5655f` |

```powershell
.\.venv\Scripts\python -m devkit.cli unpack C:\Firmware\fwdc243b.bin --output C:\Lab\gr3x-unpacked
```

After checking both complete word sums, the decoder writes `payload.bin`. It
extracts `kernel.zImage` and `board.dtb` only from validated ROMFS inode paths and
extents. GR III/IIIx also yield `ramdisk.cpio.gz` from `/initramfs`, with a checked
gzip stream and CPIO header. That ROMFS path was absent in the tested GR IV image;
this does not establish that the camera has no root filesystem. None of these
Ricoh samples has locally verified kernel, userspace or UI boot. Pentax packages
use other formats and are outside this reader's coverage.

验证完整输入、解码输出的字加法校验后生成 `payload.bin`；仅沿已验证的 ROMFS
inode 路径与范围提取内核、设备树。III/IIIx 的 `/initramfs` 还生成经 gzip 与 CPIO
头校验的 `ramdisk.cpio.gz`。实测 IV 包没有该 ROMFS 路径，不能据此认定其没有根
文件系统。这三份样本均未验证内核、用户空间或 UI 启动；宾得包属于其他格式。

## Evidence and next board work / 证据与板级开发

- **Sony A7 IV 2.00** input: 896,712,088 bytes,
  SHA-256 `394ac0daaeb9ce3f6ca0273a18c6af26505770e3b470f70d1d9f062e257bbe88`.
  Actual AES/FDAT checksum validation passed. FAT directory parsing located an
  ARM64 Linux 4.9.186-scel+ Image. Its DTB places RAM at `0x400000000`, GIC at
  `0xf13a1000`/`0xf13a2000`, PL011 at `0xf1000000`; these differ from QEMU `virt`.
  The first board implementation needs interrupt/timer/UART and storage, followed
  by proprietary IPC and display. A new firmware version requires another check.
  A bounded single-core Cortex-A35 / GICv2 trial also produced no UART. One short
  instruction trace proved execution of the original Image, through `0x40080000`
  to a Sony routine at `0x404e8730`. After its store to `0xFE005010`, execution
  entered the `0x200` exception path. The routine writes secondary-core entry
  addresses and executes DSB/SEV, suggesting a boot mailbox; without ESR evidence,
  the exact exception classification remains an inference. `nosmp` is parsed later
  and does not bypass this routine. No kernel instructions were patched.
- **Ricoh III / IIIx**: Linux 4.4.15 ARMv7, `CONFIG_ARCH_MILBEAUT`, SN_USIO UART;
  virtio and generic Versatile/Vexpress support are disabled. **IV** uses Linux
  5.15.190 ARMv7 with legacy LZ4. Parsing a valid DTB is not implementing that SoC.
  The camera RTOS main program and Linux service core also need separate treatment.
  A 10-second original-DTB `arm-virt` test produced no serial output; it does not
  prove that no guest instructions executed. Kernel and UI startup remain unverified.
- **Insta360 X3**: valid ARM64 Image, SquashFS and Ambarella H22 DTB were extracted.
  An original-DTB `arm64-virt` probe exited with no UART bytes; kernel/userspace/UI
  remain unverified. Implement the actual board rather than infer success from
  exit code. Other encrypted Luna/MWFI packages are explicitly unsupported.
- **Canon**: the original updater can be decoded, but EOS 50D main-image decoding
  and full ROM reconstruction are still missing. qemu-eos expects model-specific
  4 MiB ROM0 and 8 MiB ROM1 dumps. Stock QEMU's `canon-a1100` is an A1100/barebox
  target, not an EOS firmware adapter.

Local experiment logs and screenshots are kept outside version control. Portable
bundles include no decrypted payloads, recovered tables or proprietary artwork.
本地日志与截图留在 Git 忽略目录；便携包不含解密载荷、恢复的表或厂商资源。

## Credits and boundaries / 致谢与边界

| Research / 研究 | Pinned source / 固定来源 | How used / 使用方式 |
| --- | --- | --- |
| ma1co / DavidBuchanan314 Sony fwtool | [72fbdd15](https://github.com/DavidBuchanan314/fwtool.py/tree/72fbdd15ddcc2d2a24ccb52cf8e84b8de98c738a), [PR 52](https://github.com/ma1co/fwtool.py/pull/52) | MIT constants retained; bounded stream parser written here / 保留 MIT 常量声明，独立编写流解析器 |
| WonderLantern Nikon key/QEMU research | [key tool](https://github.com/WonderLantern/nikon-fw-key-tool/tree/33426e892afd329a3b366b73776bcf53b9ac4ee8), [Z7](https://github.com/WonderLantern/z7-qemu/tree/97abf0b81cd3211f8374a38987a0beaf4549b90f) | Independent package reader; tools/runtime installed separately / 独立包解析器，工具与运行时单独安装 |
| yeahnope / hhornbacher Ricoh | [gr_unpack](https://github.com/yeahnope/gr_unpack/tree/25f0cd513876655f355e7587302a5dd7b9ee6620), [gr3x-fw-hack](https://github.com/hhornbacher/gr3x-fw-hack/tree/dc84ad50a0e5afc86d7273eb75e86ff889b83b19) | Format/IPC research; no unlicensed source copied / 格式与 IPC 参考，未复制无许可证源码 |
| **Radium Wang** Ricoh GR IV and Hasselblad | [GR IV](https://github.com/radium-wang/ricoh-gr4-firmware-analysis-and-feature-expansion/tree/e0e0b69a71d8d7727d2b5c97c9e809a3d8f45c85), [Hasselblad](https://github.com/radium-wang/Hasselblad-X-System-CIM-Firmware-Research-Feature-Extensions) | Credited research; current GR IV restricted source not copied / 保留研究致谢，未复制 GR IV 受限源码 |
| Arm.Indy / Magic Lantern Canon | [fir_tool2.py](https://github.com/reticulatedpines/magiclantern_simplified/blob/142472e95054d73640f50789ac0fd66325499d82/tools/indy/fir_tool2.py), [qemu-eos](https://github.com/reticulatedpines/qemu-eos/tree/4b667a1d3c08ab7a55835d15ddbd884fa754946d) | GPL-2.0-or-later updater constants/algorithm; separate emulator / GPL 升级器常量和算法，模拟器单独准备 |
| nneonneo Insta360 X3 | [3e95fa30](https://github.com/nneonneo/Insta360-X3-Firmware-Tools/tree/3e95fa30f8ad10f374c98b06a950f05eeb294aaf) | Independent format parser; no unlicensed source copied / 独立格式解析器，未复制无许可证源码 |

Legacy Nikon FR/TX + ITRON emulation is a separate architecture. Recent Nikon
encrypted formats, modern Canon FIR, other Insta360 models and Pentax formats
are not covered by the tested readers. Fujifilm, Panasonic, OM System and GoPro
remain future platform work with no local boot claim.

老尼康 FR/TX + ITRON 是另一套架构。较新的尼康加密格式、现代佳能 FIR、其他
Insta360 和宾得格式未被这些实测解析器覆盖。富士、松下、OM System、GoPro
仍需独立开发与验证。
