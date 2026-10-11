# X2D II Video Research / X2D II 视频开发

Status: **offline feasibility research; physical 4K60 capture is unverified**.

This is the next camera development target for QEMU Everything: progressive
3840×2160 video at 60 independent frames per second, captured by an X2D II and
written as a playable file. The current QEMU adapter can support interface and
application development; it does not emulate the sensor, ISP or video encoder.

## 本轮结论

**值得继续研究，但目前不能承诺 X2D II 能实现原生 4K60p 录像。**
固件包含硬件编码驱动、H.264/H.265 编码库以及共用框架的录制实现。
当前机型配置没有连接成完整的 4K60 拍摄链；原厂 D-Bus 录像入口还是
明确返回错误的桩函数。

哈苏的[官方技术参数](https://cdn.hasselblad.com/f/77891/x/15b206e1b9/x2d_ii_100c_datasheet_multi_20260416.pdf)
没有列出视频录制规格。照片连拍规格不能用来推导视频读出帧率。
现有预览帧率、共享 SDK 的格式枚举，也不能直接证明录像能力。

## 证据范围

- 审计日期：2026-10-11。
- 机型：Hasselblad X2D II 100C。
- 固件：**1.2.7.16**，文件名 `X2DII_100C_v1_2_7_16.cim`。
- 固件 SHA-256：`68aa9bce63c303a7d13ca4c14aae76be935925d596094bdcb92549cff42e1bc1`。
- 本轮仅进行离线读取、配置分析和 AArch64 反汇编，没有执行相机代码。
- **1.3.16.2 尚未完成同等审计**；API、地址和配置结论不能直接外推。

下面路径是解包固件内部的路径。仓库不包含相机固件、专有二进制或
完整厂商配置；本页仅记录分析结论与定位信息。

## 已确认与尚未确认

| 位置 | 已确认的证据 | 对开发的含义 |
| --- | --- | --- |
| `/bin/camera-service` | `CameraObjectImpl::doStart_recording` 与 `doStop_recording` 都位于 VA `0x1f1400`，长度 204；加载 `Not implemented` 并发送 D-Bus 错误 | 调用原厂录像方法不会开启录像；需要自己的录制后端 |
| `/lib64/libdcam_frwk.so` | 有真实代码的 `cam_cap_eng_set_recording_params`、`get_recording_capability`、`start_recording` 和 pause/resume/stop/wait/split API | 是值得逆向的录制框架入口；参数结构和生命周期尚未还原 |
| `/lib64/camera/plugins/shooter/libdcam_shooter_video_single.so` | 有 `capeng_create_single_video_shooter` 及录制流 start/data/stop 回调 | 框架保留了视频 shooter；当前 IMX861 配置没有选用它 |
| `/lib/modules/vc_encoder.ko` 与 `/lib64/lib_vc_encoder.so` | VC8000E 标识、H.264/H.265 流编码 API、ASIC 能力查询及硬件功能缺失检查 | 可以研究硬件编码；实际核心、fuse、分辨率和吞吐能力需要设备证据 |
| `/lib64/libdcam_media.so` | 有 `3840x2160P6000`、`3840x2160P5994` 等格式枚举 | 是共享 SDK 格式定义，尚未发现它们成为本机有效录制预设 |
| `/etc/imx861bqr_hb722.conf` 的 `video_4x3_14bit_f2f` | RAW opaque 输入配置为 7408×1482 / `DCAM_FPS_6000`；显示输出为 1300×970 / `DCAM_FPS_6000` | 已配置 60fps 预览路线；输出不是 UHD 4K，配置帧率尚未实测 |
| `/etc/imx861bqr_hb722.sp` 的 `f2f-liveview-template` | `raw-reprocess` shooter；输入经过 reprocess 后送显示引擎，没有编码器和文件 writer | 这一组的 video 名字不能解释为已有电影模式 |
| 普通预览的 `.conf` / `.sp` | 已编码预览配置为 1300×970 / 30fps，经 RTP 路线输出 | 可以作为低分辨率短片开发的技术起点；需明确标注实际规格 |
| `/etc/imx861_sensor_test.json`、`imx861_sensor_evaluate.json` | 有名称含 `4K2K ... 2P/6P/8P/9P` 和 `11K1K ... 60P` 的模式 | 还没有证明存在 4K60 传感器模式；需要还原 mode 表、采样方式和实际时序 |

`RAW16_OPAQUE` 的缓冲区尺寸不能直接当作有效图像尺寸。必须确认
打包格式、步长、裁切和采样规则。固件出现 IMX861 驱动和配置，也
不足以独立确认实机传感器的全部型号及能力。

[VeriSilicon VPE](https://github.com/VeriSilicon/vpe) 提供基于 VC8000 编解码
IP 的实现资料，可帮助理解接口。其他平台的 4K60 指标不等于这台相机
所集成核心的性能；相机驱动 ABI 也需要单独匹配。

## 可复核的定位

这些地址只对应本页记录的固件。ELF 虚拟地址不是磁盘文件偏移。

- `camera-service` SHA-256：
  `23cb51e2abd7414933aaaf4387038f48e1574c487120383cb13d5a1d5ff1104c`。
- 录像桩函数：`0x1f1400`；错误字符串 `0x6a87de`；
  `QString::fromLatin1` 调用目标 `0x44d4e0`；
  `Bus::createSendErrorReply` 调用目标 `0x57c150`。
- `libdcam_frwk.so`：set recording params `0x1a800`；get recording capability
  `0x1adb0`；get recording params `0x1b180`；start recording `0x1b550`。
- `lib_vc_encoder.so`：`VCEncGetAsicConfig` `0x275c8`；
  `EWLReadAsicConfig` `0x5efa8`；`EWLReadAsicID` `0x5ee88`；
  `EWLGetCoreNum` `0x5ee18`。
- `.conf`：60fps f2f 组 2733–2759 行；普通预览 239–265 行。
- `.sp`：f2f 模板 2313–2361 行；普通预览编码/RTP 715–778 行。

反汇编使用 pyelftools 0.33 与 Capstone 5.0.7。编码器和框架查询函数
目前只做了离线定位，尚未在实机调用；函数名含 query/get 不代表无副作用。

`cam_cap_eng_get_recording_capability` 的正常路径要求已有有效引擎对象，
读取 `eng->cur_strm_grp_ops`，再调用其 `get_recording_capability` 回调。
它不能用空参数自动发现硬件；无效对象或缺失回调会走致命断言并退出。
输出结构尺寸、返回类型和间接回调的副作用仍未确定，需要先还原流组
创建与回调初始化，再设计隔离探针。

## 开发顺序与通过条件

| 阶段 | 工作 | 通过条件 |
| --- | --- | --- |
| 1. 能力调查 | 还原 sensor mode 表、录制 API 参数、编码器能力查询 ABI；核对实机板型与固件 | 查询方式和结构体已验证；得到实际传感器读出、ISP 输出与编码器约束 |
| 2. 帧流验证 | 在隔离的临时流程中测试传感器到 ISP 的帧流；记录尺寸、位深、采样方式、时间戳与丢帧 | 3840×2160 有效图像、连续 60 个独立帧/秒；明确视角与裁切，不能靠重复帧满足指标 |
| 3. 编码验证 | 连接 ISP 输出和硬件编码器，确认 DMA/ION 缓冲区、同步及所有权 | 实际 H.264 或 H.265 码流可解码，尺寸和帧率正确，记录编码丢帧与耗时 |
| 4. 文件录制 | 短时写入独立测试文件，完成时间戳、封装、停止和异常清理 | 文件在相机外可播放；停止后原厂 UI 与拍照恢复；记录存储、温度和电源表现 |
| 5. 操作界面 | 用 QEMU 开发录像设置和状态页，再接入真机已验证的后端与 dock | 显示实际支持规格、录制状态、剩余空间和错误；停止/退出完整回收资源 |

首个实机原型应有录制时限、独立输出目录和完整停止路径。工厂 sensor
测试脚本会停原厂服务并切换测试模式，不能直接作为应用启动流程。
软件交换空间不能提高传感器或 ISP 的吞吐率；CPU 编码也不能预先视为
4K60 的可用替代方案。

若某一阶段无法满足目标，报告该阶段测得的上限，并将可运行的原型按
真实分辨率和帧率命名。59.94p 与 60.00p 需要分别验证和标注。

## 当前交付状态

- 离线可行性审计：已完成上述范围。
- 实机能力探针：待开发及验证。
- 4K60 拍摄、编码和写文件：未验证。
- 录像应用与相机安装包：尚未实现。

## English summary

Firmware 1.2.7.16 contains a VC8000E encoder driver, H.264/H.265 APIs and a
shared recording framework. The factory D-Bus start/stop recording methods
return an explicit unimplemented error. The configured 60fps f2f path outputs
1300×970 live view and has no recording writer. Shared 4K60 format names and
sensor test preset names do not establish a working capture mode.

Development must first establish a real sensor-to-ISP 3840×2160/60p stream,
then verify hardware encoding, file output, cleanup and sustained operation on
the physical camera. QEMU is useful for the application interface; it cannot
validate this camera's imaging throughput. No recording support is advertised
as implemented, and the findings have not yet been repeated for 1.3.16.2.
