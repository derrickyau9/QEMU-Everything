# QEMU Everything

**导入固件、分析格式、建立支持工作区，在 QEMU 中启动兼容的内核、磁盘镜像或哈苏原厂界面。**

本项目原名 **Hasselblad X System QEMU DevKit**，保留原有相机 UI 适配器，新增通用固件库和可审阅的启动配置。QEMU Everything 表达项目的发展方向；每一份专有固件能否启动，仍需要按硬件平台验证。

[English](README.md) · [发行版本](https://github.com/derrickyau9/QEMU-Everything/releases) · [哈苏官方固件](https://www.hasselblad.com/zh-cn/x-system/firmware/)

这里是 QEMU Everything 的新仓库。在此发布便携版之前，请使用下方的源码启动方式。

![QEMU Everything 中保留的哈苏原厂 UI 适配器](docs/workbench.png)

## 快速开始

1. 源码版双击 `Launch.cmd`。新版便携包名称为 **QEMU-Everything-Windows-x64.zip**，主程序为 **QEMU-Everything.exe**；已发布的旧版本可能仍使用原名称。
2. 从厂商官网下载固件，将本地文件拖入工作台。导入会记录 SHA-256、可识别的格式和架构，并建立固件库条目。
3. 已识别的哈苏 `.cim` 使用原有的 **原厂 UI** 适配器；兼容的通用内核或磁盘镜像使用 **启动配置**。未知或加密升级包可以先分析、建立支持工作区。
4. 审阅配置，准备所需运行环境后启动。通用虚拟机显示在 QEMU 独立窗口，串口输出进入工作台控制台；相机 UI 保留内嵌触控和截图。

[通用固件说明](docs/GENERAL_FIRMWARE.md) · [架构说明](docs/ARCHITECTURE.md)

启动支持版本的哈苏原厂 UI 时，首次使用仍需阅读并接受 Android SDK 许可条款。鼠标点击模拟触控，拖动模拟滑动；点击 **停止** 关闭该虚拟机，之后使用缓存即可离线启动。

相机 UI 运行环境下载约 **615 MB**。需要 Windows x64、建议至少 8 GB 内存、预留 **8 GB 磁盘空间**。通用虚拟机可单独准备 QEMU，无需 Android SDK。所有运行文件放在用户目录，不需要管理员权限。

## 已实现

- 文件拖拽导入、SHA-256 身份校验、格式与架构分析、本地固件库。
- 为新固件建立支持工作区，记录待补的启动条件。
- ARM64 `virt`、ARM `virt`、x86-64 `pc`、RISC-V 64 `virt` 的 JSON 启动配置；启动匹配的内核或磁盘镜像，磁盘写入使用临时快照、关闭网络。
- 命令行分析、导入、列出平台、启动、导出支持信息。
- 离线校验与解包索尼、尼康、理光、佳能、Insta360 的指定机型固件。
- 使用单独构建的定制 QEMU，启动已验证的尼康 Z7 C3.80、Z30 C1.20、Z6 C3.80、Z50 C2.60 原厂菜单实验。
- 启动 Insta360 X3 原厂 emWin 界面，已验证触摸切换 72MP → 18MP → 72MP。
- 导入 GoPro HERO8 2.51 官方升级包，配置独立 UI 平台后运行原厂 Tara 拍摄页与 VIDEO 菜单。
- 校验并离线解包适马 fp 5.02 的 DFI/LZSS 主程序；输出已与原厂解压函数执行结果逐字节核对，UI 启动仍在开发。
- 从工作台或 CLI 限时验证索尼 A7 IV、理光 GR 和 Insta360 X3 的原厂 Linux/根文件系统启动。
- QEMU 中原厂 UI 实时画面、鼠标触控、启动 / 停止、PNG 截图。
- 中文 / English、跟随系统 / 浅色 / 深色、紫 / 蓝 / 绿主题。
- 开发工作区、按机型选择架构的 `hello.c` 示例、NDK clang 编译及虚拟机运行。
- 在本地 QEMU 中导入、选择和移除经过校验的第三方 Wayland 应用包。
- 可为 X1D 或 X2D II 虚拟机应用会话构建[NetSurf 浏览器原型](docs/BROWSER_PROTOTYPE.md)，支持触摸键盘和 X2D II 可选 QEMU 网络。
- 虚拟机 Shell 控制台和日志目录。
- 每份固件独立的数据盘，系统改动使用临时快照。

## 支持范围

| 项目 | 状态 |
| --- | --- |
| Windows x64 | 支持；原生 Qt 便携程序 |
| macOS Apple Silicon | 支持从源码运行，见 [Mac 安装说明](docs/MACOS.md)；暂未提供便携包 |
| X2D II 1.2.7.16 | 原厂 UI、主菜单和 Display 子菜单已验证 |
| X2D II 1.3.16.2 | 见[验证记录](docs/VERIFICATION.md) |
| 新增机型 | X1D、X1D II、X2D 和 907X 50C，见下方预览版矩阵 |
| 通用内核 / 磁盘镜像 | 提供启动配置，镜像必须匹配所选 QEMU 平台 |
| 未知 / 专有升级包 | 可分析并建立支持工作区，尚未验证启动 |
| 尼康 Z7 C3.80 / Z30 C1.20 | 原厂菜单、进入子菜单与返回已验证；辅助启动，独立定制 QEMU / WSL |
| 尼康 Z6 C3.80 | 原厂回放菜单及向下/向上选择往返已验证；辅助启动；LCD 扫描输出、自然开机与成像未验证 |
| 尼康 Z50 C2.60 | 同一虚拟机内 MY MENU → Add items → MY MENU 已验证；辅助启动，自然开机与成像未验证 |
| 索尼 A7 IV 2.00 | 原厂 Linux/rootfs shell 已启动；独立组件实验已验证[原厂诊断控件、图标、字体及指针响应](docs/sony-a7iv-diagnostic-ui-verification-20261011.json)；普通相机菜单与完整开机未验证 |
| 索尼 A7R V 4.00 | 原厂 Linux 4.9.337-scel 与 ext2 根文件系统 shell 已启动；独立组件实验已验证[原厂诊断控件、图标、字体及指针响应](docs/sony-a7rv-diagnostic-ui-verification-20261011.json)；普通相机菜单与完整开机未验证 |
| 理光 GR III 2.10 / IIIx 1.60 | 原厂 Linux/根文件系统 shell 已启动；camctld 处于 restrict 模式；RTOS IPC 与相机 UI 未验证 |
| 理光 GR IV 1.11 | [原厂 TopMenu → Focus 设置 → 返回已验证](scripts/boards/ricoh/evidence/griv-1.11-menu-roundtrip.json)，支持原生上下键与完整像素恢复；辅助组件已接入工作台，完整冷启动与拍摄未验证 |
| Insta360 X3 v1.1.4.1_build1 | 原厂 emWin 分辨率菜单与触摸往返已验证；Linux root shell 也可启动；完整 ThreadX/跨核启动与拍摄未验证 |
| 佳能 EOS 50D 1.0.9 | 原厂 DryOS 升级器已启动；主固件/UI 仍需完整 ROM 与 ROM1 种子 |
| 佳能 EOS 70D 1.1.2 | 原厂 ROM 从复位启动，显示 720 × 480 佳能菜单，原生上下键选择往返已验证；需要精确匹配的完整 ROM 与串行闪存数据，仅官方升级包不足以启动 |
| GoPro HERO8 Black 2.51 | 同一虚拟机内原厂 Tara 拍摄页 → VIDEO 预设菜单 → 返回已验证；属于 UI 组件辅助启动，原厂冷启动与录制未验证 |
| 相机 UI 渲染 | QEMU TCG CPU 模拟 + Qt Quick 软件渲染 |
| 相机 guest 硬件加速 | 当前 ARM64 guest 在 x86-64 Windows 上未实现 |
| 拍摄、ISP、对焦、镜头、真实存储 | 未模拟 |
| 实机安装、刷机 | 不提供 |

哈苏适配器的运行方式是：**在开发虚拟机中运行原厂相机 UI，按固件选择 Android ARM32、Android ARM64 或 Linux ARMhf 运行库，并提供模拟相机服务**。它没有启动相机原厂内核，也不等于完整相机硬件仿真；部分功能不会响应，数值可能为模拟值，流畅度取决于电脑 CPU。尼康菜单实验调用原厂函数并修改 guest RAM；自然开机与完整拍摄硬件未验证。

配置对应的独立构建后，**原厂 UI 实验室 / Original UI lab** 会精确选择尼康、X3、佳能 70D、理光 GR IV 或 GoPro HERO8
适配器。参见 [Z50 启动说明](scripts/boards/nikon-z50/README.md) 和
[X3 启动说明](scripts/boards/insta360/UI_README.md)。X3 执行原厂控件、字体和事件
处理，以 8 处共 68 字节的 RAM 平台适配代替调度、时钟、显示提交与消息投递；
固件文件不变。原厂菜单成功运行不等于完整硬件开机、实时取景或视频录制。

[佳能 EOS 70D 1.1.2 实验](scripts/boards/canon/README.md)从原厂 ROM 复位启动并显示
原厂菜单，原生上下键选择、工作台启动与关闭清理已验证。需要另外提供三个精确匹配的
ROM/串行闪存文件，官方升级包不足以启动。子页面、标签切换与拍摄尚未验证。

索尼 A7 IV 与 A7R V 诊断组件属于独立研究实验：使用原厂控件、资源与字体及软件外设适配，
诊断文字由开发者提供。它尚未接入 **原厂 UI 实验室** 按钮；普通相机菜单、
完整开机均未验证。

[理光 GR IV 组件实验](scripts/boards/ricoh/README.md)已接入 **原厂 UI 实验室** 按钮。
配置独立构建后，导入精确匹配的官方 1.11 `fwdc248b.bin`，程序自动解包并验证原始数据。
系统 QEMU 执行原厂 TopMenu、Focus 设置页、字体与图标，提供只读资源和软件显示接口；
原生上下键、确认进入和返回均已验证。辅助接口调用原过渡完成函数；完整 RTOS 动画计时、
冷启动、物理 LCD、拍摄与跨核 IPC 未验证。

新增 Linux 实验使用独立定制 QEMU 板型、开发设备树和明确的引导设置。导入、解包匹配固件后，按[多品牌相机固件记录](docs/CAMERA_FIRMWARE.md)配置本地构建。点击 **启动相机 Linux 实验**运行 30 秒验证，**取消**停止该任务拥有的虚拟机；串口启动证据与相机 UI 状态分别记录。

各品牌的真实样本、启动结果、命令与研究来源见[多品牌相机固件记录](docs/CAMERA_FIRMWARE.md)。

识别文件格式或 CPU 架构，只能说明分析结果。原始系统启动还需要匹配的主板、启动链及驱动；通用 `virt` / `pc` 配置不能替代相机专有 SoC。未知固件建立草稿支持工作区，供后续调查、记录这些依赖。相机 UI 的已验证范围见[验证矩阵](docs/VERIFICATION.md)。

程序按固件在独立开发副本中适配内嵌 QML 的图片可见性或旧版根窗口，以支持软件渲染。原始可执行文件保留，不修改机器指令，不回写 `.cim`。详见[架构说明](docs/ARCHITECTURE.md)。

## 新增机型（v0.2.0）

| 机型 | 可识别固件版本 | 运行方式 |
| --- | --- | --- |
| X1D 50C | 1.20.0、1.21.0、1.25.0 | Linux ARMhf，640×480 原厂界面 |
| X1D II 50C | 1.0.1、1.0.2、1.1.0、1.2.0、1.3.0、1.4.0、1.5.2 | Android ARM32 |
| X2D 100C | 1.0.5、4.2.0 | Android ARM64 |
| 907X / CFV II 50C | 1.5.2 | 与 X1D II 共用固件，在固件库中选择机型 |

v0.2.0 提供这些机型的 UI 开发预览支持。**不包含 907X / CFV 100C。** 本次提供的 X1D II 与 CFV II 1.5.2 文件内容完全相同，因此合并成一个固件条目，提供两种机型选择。

X1D 首次启动额外下载约 78 MB 的 Debian ARMhf 运行库，之后可离线使用；初次安装建议预留 12 GB，多份固件需要额外空间。它通过虚拟机内的 Qt 5.15 兼容环境运行原厂 Linux UI，没有模拟相机原厂内核及硬件。F1–F5 按钮发送原厂 UI 对应的虚拟按键；启动后自动进入主菜单，便于开发。

编译器会根据固件自动选择 Android ARM32、Android ARM64 或 Linux ARMhf，均使用 NDK r27 的 clang；Linux 另使用自动准备的 sysroot。主菜单和设置页能打开不代表所有硬件功能都可用，详细实测结果见[验证记录](docs/VERIFICATION.md)。

## 开发流程

1. 导入并选中固件。
2. 打开 **开发工作区 → 打开开发工作区**，编辑 `hello.c`。
3. 选择 [Android NDK](https://developer.android.com/ndk/downloads) 目录（已验证 r27）。
4. 停止虚拟机，点击 **编译应用 → 运行应用**。
5. 示例在独立虚拟机会话中运行；停止后重新启动原厂 UI 即可返回。

仅启动原厂 UI 不需要 NDK。示例是按机型编译的 Wayland 共享内存客户端。开发自定义 Qt guest 应用另需与所选固件 ABI 匹配的 Qt 工具链，本项目不附带该工具链；Windows Qt 可执行程序不能直接在虚拟机里运行。

[开发说明](docs/DEVELOPMENT.md) · [故障排查](docs/TROUBLESHOOTING.md)

下一项相机研究目标是 **X2D II 原生 4K60p 录像**，目前处于离线可行性调查，
尚未实现拍摄。固件证据与开发顺序见 [视频开发记录](docs/X2DII_VIDEO_RESEARCH.md)。

可用 [`scripts/app_packages.py`](scripts/app_packages.py) 将目标架构的
ELF 程序打包成 `.xdevapp`，再在开发页导入。应用包必须匹配所选机型与
ABI；它只在独立的本地 QEMU 会话运行，不会安装到实机。

## 从源码运行

安装 Python **3.11+**，克隆仓库，双击 `Launch.cmd`。首次自动建立 `.venv` 并安装依赖。

```powershell
git clone https://github.com/derrickyau9/QEMU-Everything.git
cd QEMU-Everything
.\Launch.cmd
```

新用户数据默认在 `%LOCALAPPDATA%\QEMU-Everything`；已有的 `%LOCALAPPDATA%\X2DII-DevKit` 固件库会自动沿用。可通过 `QEMU_EVERYTHING_HOME` 环境变量或 `--home <目录>` 改位置；旧的 `X2DII_DEVKIT_HOME` 及启动命令继续兼容。路径不要含英文逗号。固件、解包文件、运行时、日志和个人开发目录不提交到 Git。

在 Apple Silicon Mac 上，按[Mac 安装说明](docs/MACOS.md)准备 QEMU 和 Android 镜像，然后运行：

```sh
./Launch.sh --home "$HOME/Library/Application Support/QEMU-Everything"
```

源码安装后的命令行示例：

```powershell
qemu-everything inspect C:\Firmware\update.dat
qemu-everything boards
qemu-everything import C:\Firmware\Image
qemu-everything support <固件目录>
qemu-everything run <固件目录>
```

分析不会执行用户提供的固件。通用启动需要有效配置及 QEMU；新镜像先按[配置流程](docs/GENERAL_FIRMWARE.md)准备。

## 致谢和许可证

- **[Radium Wang / radium-wang — Hasselblad X-System CIM Firmware Research & Feature Extensions](https://github.com/radium-wang/Hasselblad-X-System-CIM-Firmware-Research-Feature-Extensions)**：为 CIM 固件离线分析、原厂 UI 与菜单扩展研究提供了参考。感谢 Radium 公开分享相关研究成果。
- **[Derrick Yao / tsla-infotainment-lab](https://github.com/derrickyau9/tsla-infotainment-lab)**：Material 主题及 Qt 工作台设计来源，保留 MIT 版权声明。参考版本 `f590d8fd9a053ab072df94b994c0a58ee7b976ed`。
- 基于 Derrick Yao 已有的 X2D II QEMU / UI 研究及 CIM 离线容器工具。
- QEMU、Stefan Weil 的 Windows 构建、Android / AOSP、Qt for Python、7-Zip、Dissect 等开源项目。

QEMU Everything 使用 **AGPL-3.0-or-later**，满足 Dissect 文件系统依赖的许可证要求；改编的主题保留原 MIT 声明。便携包附带项目源码和第三方许可，Qt 动态库可独立替换。详见[第三方声明](THIRD_PARTY_NOTICES.md)。

厂商固件、字体、图片、动态库及商标归各自权利人所有，**不随仓库或发布包分发**。用户自行提供官方固件。本项目与哈苏或其他固件厂商无隶属或背书关系。

## 免责声明

本项目按“现状”提供，不作任何保证。使用及修改产生的风险由使用者自行承担；**如导致保修失效、相机损坏、数据丢失或其他损失，后果自负**。DevKit 本身只运行本地虚拟机，不提供实机安装或刷写功能。
