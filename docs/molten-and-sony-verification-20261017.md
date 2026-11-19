# MOLTEN 最小纵向链与 Sony 启动实测 · 2026-10-17

基线为 `051af09273e115918382ab21a4594d3fc154a9aa`，本地分支为
`codex/molten-minimal-chain`。本轮已完成随机布局 AArch64 微固件的观察、
局部 SMT、反例精化、原生状态模型、输入／像素验证及无求解器重放。
Sony 已实测原厂 Linux 启动，并恢复了一个真实原厂 IPC 注册生产者；
普通相机菜单和原始存储冷启动仍未通过。

## 本机直接运行

以下命令在 PowerShell 执行，使用本轮已构建、校验的 Debian WSL 环境。
每次 Explore、Replay、Test 使用独立输出目录。

```powershell
$runner = 'F:\New project\QEMU-Everything\.local\molten\run-verified.ps1'
& $runner -Mode Replay
& $runner -Mode Test
& $runner -Mode Explore
& $runner -Mode SonyShell
& $runner -Mode SonyInit
& $runner -Mode SonyPcieProbe
```

`Replay` 执行冻结配方的四组控制与十五次序列重放；`Test` 执行自适应与
Sony 相关回归。`SonyShell` 和 `SonyInit` 各观察十五秒。
`SonyPcieProbe` 在一个新的 768 MiB 私有开发镜像副本中观察三十秒；
它验证原厂模块注册，保留原始镜像。`SonyCoupled` 可复跑既有私有 AP／CP
联合环境的二百秒实验，另有最多一百八十秒 CP 预启动预算。

便携构建与源码命令见 [最小链说明](molten-minimal-chain.md)。
本机证据总索引为
`F:\New project\QEMU-Everything\.local\molten\delivery-evidence.json`，包含各文件
SHA-256、原始目录与汇总。一份较大的 CP 事件 JSON 保留于原目录，索引记录
其完整路径、大小及 SHA-256，其余主要日志已复制进 `.local/molten/sony-evidence/`。

## MOLTEN 实测结果

| 检查 | 实际结果 |
|---|---|
| 自适应契约／求解／原生集成／负例 | 当前完整 `tests/adaptive`：95 passed，Debian WSL；Windows 78 passed、10 skipped（缺少原生 QEMU 环境） |
| 既有 Sony 组件、lab、board assets 回归 | 43 passed，Debian WSL |
| 公开完整配方重放 | 4 个控制 + 3 条序列 × 5 次，19 次干净启动 |
| 冻结结果一致性 | CPU／寄存器／MMIO／虚拟时间／像素签名一致 |
| 无求解器条件 | 独立进程不导入 Z3、Capstone、Triton；另有禁止导入测试 |
| 固定返回模型 | 后续不同输入产生反例并被拒绝 |
| 状态模型 | 自动合成并在 QEMU 原生执行一个共享 `xor_latch` |
| Wheel | 构建成功，16 项新增资源／入口／依赖核验通过 |

原先失败的种子 `104729` 已纳入通过的原生参数化回归；另两个随机布局种子
`731`、`8675309` 也通过，每个种子执行 15 次无求解器重放。研究机把 DTB 固定到
`0x47f00000..0x48000000`，栈保留在其下方，ELF 可以使用 RAM 起始的第一 MiB。
同时补上 `.inc/.dts` 的 LF 属性，避免 Windows 换行转换破坏固定构建指纹。
局部求解 timeout／unknown 保留为预算耗尽；恢复 campaign 重新检查插件准入，
并绑定状态、报告和配方身份。旧 pending 任务按相同 continuation 接管，
新尝试使用独立运行目录与干净重启。

验收同时拒绝无像素的 present、静态 logo、未支持指令、配方漂移和失败的能力
探测。结果是有限微固件契约下的 `SUCCESS_TESTED / scope=unclassified`。
这套初始文法只支持一个 32 位写入锁存器和一个读取端口；尚未实现通用 Sony
Linux 进程、DMA／IRQ／IPC 合成和普通菜单发现。十分钟 Sony 持续交互验收
仍作为未完成义务保留。

相关实际产物：

- `.local/molten/verification-tests.log`
- `.local/molten/sony-regression-tests.log`
- `.local/molten/verified-campaign/report.json`
- `.local/molten/verified-campaign/recipe/recipe.json`
- `.local/molten/verified-public-replay/report.json`
- `.local/molten/packaging-wheel-contents.json`

## Sony 本轮真实执行

A7 IV 2.00 固件 SHA-256：
`394ac0daaeb9ce3f6ca0273a18c6af26505770e3b470f70d1d9f062e257bbe88`。

| 路线 | 本轮证据目录名 | 实际观察 |
|---|---|---|
| 原厂 kernel/rootfs + shell | `8ddc70d923af4bc6bb843034b8b18543` | rootfs 挂载、BusyBox 与串口命令证明均通过 |
| 原始 init | `80ec472db71b4162bf6d57d684da6e8e` | 进入 init／DMA 初始化；crypt-emmc 外部读异常 |
| 私有 AP/CP，60 秒 guest | `b8a280f4d51f49f69ccbfe50a318e4dc` | 开发 init2 流程推进；未出菜单 |
| 私有 AP/CP，200 秒 guest | `5fe091e8903942ac88db7f6f4dbcef92` | 69 个 actual-wire 事件、20 次 pin query、10 次 pin connect、9 次 HDMI reply；未出菜单 |
| PCIe 初始化初试 | `e080566899b44a9c9b4f37125f11c99a` | 模块加载；暴露缺少原厂 cpuset 的依赖 |
| PCIe 注册生产者证明 | `5d67b0e39a7e4d468ca52b3cc2ae8411` | 原厂 cpuset 与模块加载成功；三个只读指针相等，原镜像未变 |

公共 shell 路线采用已有板级模型，属于 F2 辅助启动；私有联合和生产者实验
采用已解密开发媒体、原厂组件与额外兼容知识，属于 F3。联合路线还含既有
LD_PRELOAD 兼容层和 ARM32 CP sidecar，不能计为 MOLTEN 从 F0 自动生成的方案。
生产者证明不启动 App、不使用该 preload，也没有给设备制造完成 ack。
所有本轮 guest 均在各自监督器内有界退出并被回收。

原始 init 的实际异常为 VA `0xffffffffffffb8eb`，LR 位于
`cryptEmmc_setKeyCovertly+0x40`。现有研究把它关联到物理
`0xffffb800..0xffffbbff` 的外部读源；本轮没有取得该源的可靠 backing。
因此没有用任意字节或成功返回代替解密输入。

## 新的因果发现：恢复真正的 public 注册生产者

`No snd_msg registered ... 0x86f/0x964` 的直接原因是 OSAL 的统一跨芯片
public 发送回调未注册。这些低十六位日志本身不足以证明两个独立接收端缺失。
原始 `osal_reg_public` 的注册生产者是 `pcie-if.ko`，开发 initrc 曾省略其加载。

原模块：

```text
osal_uipc.ko 0489415719bbf62d74792e605ad724b06afc75a6c5b874fccf0c80506e3ddb7f
pcie-if.ko   47278167497862a8d46cf542b8a4b91d16e0d598a41735976eab6cfdd9e18650
```

其原始初始化会清零 `0x404c00000..0x404c80000` 的队列 RAM。隔离实验先在
私有 DTB 中保留这段真实区域，避免覆盖 Linux 已分配页；再运行镜像内原厂
`setup_shielding`，加载原始 devno、OSAL UTM/UIPC、utimer 与 pcie-if。
第二次实验没有 cpuset 错误或 kernel warning，真实 insmod 返回 0，模块 Live，
IRQ 91／92 已注册但没有触发。

从 ELF relocation 得到槽／函数表的相对位置后，以 guest sysfs 的真实加载
基址计算地址，在 QMP stop 后只读取得：

```text
[0xffffff800050b558] = 0xffffff80005330b8  # OSAL public 槽 -> 原始 PCIe 表
[0xffffff80005330b8] = 0xffffff800052e4d0  # 原始 async wrapper
[0xffffff80005330c0] = 0xffffff800052e4b8  # 原始 sync wrapper
```

三者均与原厂 `.data + 0xb8`、`.text + 0x54d0/+0x54b8` 匹配。
证据在 `.local/molten/sony-evidence/pcie-callback-proof/public-callback-proof.json`。
这证明一个实际注册生产者已恢复，仍不证明跨芯片传输或 UI 工作流完成。

下一条可验证边界是 AP GPIO port18.bit9、peer-ready port17.bit4，以及
IRQ 91／92、PCI DMA、共享队列间的因果关系。原始 CP 服务激活和图形出帧也
需要继续验证。旧私有资料中把 framebuffer 文件存在标成普通 UI 成功的记录，
其实际帧为单色且缺少输入因果证据，本轮未采用这种成功判定。

随后对 PCIe GPIO、IRQ 91／92、DMA／共享队列与原厂 Menu 输入所做的有界
实验、失败记录和复跑命令，见
[A7 IV PCIe 对端与输入跟进](molten-sony-pcie-followup-20261017.md)。
通用传输契约见 [MOLTEN transport](molten-transport.md)。这些新实验仍未
取得原厂普通菜单或输入闭环证据。

## 一手研究来源与保留限制

重新核查了 Sony 的 [A7 IV GPL 源码分发页](https://oss.sony.net/Products/Linux/DI/ILCE-7M4.html)
和 [A7R V GPL 源码分发页](https://oss.sony.net/Products/Linux/DI/ILCE-7RM5.html)，
以及固定 [QEMU 10.2.4 plugin header](https://github.com/qemu/qemu/blob/v10.2.4/include/qemu/qemu-plugin.h)。
注册生产者与函数表位置还通过本机原始 ELF 的符号、relocation 和实际内存
读回互相核查；它们属于本轮 F3 研究知识，未伪装成自动发现器的 F0 成果。

Windows 原始基线还存在间歇性 `fstat/path.stat` 的 `st_ctime_ns` 差异，
在未修改的精确 HEAD 独立解档中已重现。上表的 43 项 Sony 回归和直接运行
命令均使用 Debian WSL；没有把 Windows 的这一既有问题列为已修复。
