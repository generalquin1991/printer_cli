# TE344 打印命令使用说明

[English](README.md) | 简体中文

本文对应当前项目的 `print_positions.py` 和 `probe_te344.py`，列出这两个脚本的全部命令行入口，不是打印机完整 TSPL 指令手册。

## 使用前提与纸张配置

在项目目录运行以下命令，使用已有虚拟环境 `.venv/bin/python`。脚本依赖 PyUSB 和可用的 USB 后端，通过 USB 直接连接打印机，不通过系统打印队列。

当前配置固定为：

- 设备：TE344，USB VID:PID 为 `1203:0273`。
- 分辨率：300 DPI，按 12 dots/mm 换算。
- 每行 8 列；每个标签按 5 × 5 mm 计算，列间距和行间隙均为 2 mm。
- 整行打印区域宽 54 mm、高 5 mm。
- 一行纸张节距为 7 mm，即 84 点；不是 5 mm。

## 面向程序的命令

GUI 或其他 CLI 应使用单槽接口，不依赖旧接口的 `/` 分列约定：

```sh
.venv/bin/python print_positions.py print text --data "A123" --slot 3 --font 2 --present-rows 5 --return-rows 0 --speed 1.5
.venv/bin/python print_positions.py print text --data "B456" --slot 4 --font 2 --present-rows 5 --return-rows 6 --speed 1.5
.venv/bin/python print_positions.py print qr --data "https://example.com/123" --slot 3
.venv/bin/python print_positions.py status --json
.venv/bin/python print_positions.py capabilities
.venv/bin/python print_positions.py feed 1
.venv/bin/python print_positions.py back 1 --speed 1.5
```

- `--slot` 是纸带当前一行中的目标位置，范围 1～8；不会自动寻找下一个空位。
- `--present-rows N` 默认 `5`：打印后额外前进 N 行（每行 7 mm），便于撕取；`0` 不额外前进。可对每个作业单独设置，也可用 JSON `present_rows` 设置。
- `--return-rows N` 默认 `0`，范围 `0..119`：打印前回退 N 行。当前尚未实机验证的同行定位估算为：首次打印传 `0`；之后传“上一作业的 `present_rows + 1`”。例如上一作业展示进纸 `5` 行，则估算下一作业回退 `6` 行。这个估算假定打印本身前进一行，依赖实机验证。
- `--speed` 设置打印和进退纸速度，单位 IPS，支持 `1, 1.5, 2, 2.5, 3, 3.5, 4, 5`，默认 `1.5`。独立 `feed`/`back` 也支持 `--speed`。
- `--font` 取内置字体编号 1～5，默认 2。字符内容超过 5 × 5 mm 标签边界时拒绝发送。
- 二维码使用打印机原生 TSPL `QRCODE` 指令；`--ecc` 可选 `L/M/Q/H`，`--module-size` 是模块宽度点数，范围 1～10，默认 1。5 × 5 mm 对二维码容量和可扫描性有明显限制，调用方应先用实际设备和扫码器验证内容长度及模块尺寸。
- 机器模式打印数据仅接受可打印 ASCII；文本可包含 `/`，它不再是分隔符。JSON 输入目前也只接受 ASCII 内容。
- `status --json` 只查询状态，不执行走纸。`capabilities` 不连接打印机，始终输出 JSON。
- `feed`/`back` 是显式走纸，`ROWS=0` 会直接成功且不连接设备。

GUI 可把一个 JSON 作业写入标准输入：

```sh
printf '%s' '{"type":"text","data":"A123","slot":3,"font":2,"present_rows":5,"return_rows":0,"speed":1.5}' \
  | .venv/bin/python print_positions.py print --json -
```

二维码作业示例：

```json
{"type":"qr","data":"https://example.com/123","slot":3,"ecc":"M","module_size":1,"present_rows":5,"return_rows":6,"speed":1.5}
```

成功时 JSON 打印命令在 stdout 输出 `{"ok":true,...}`；诊断信息写入 stderr。输入校验失败退出码为 2，设备或打印错误分别使用 3 和 4。实际写入 USB 后发生错误时，打印机可能已经部分执行，不应盲目重发。

旧的 `print TEXT --size N --pos N` 仍保留，供已有人工流程使用；`--next` 已停用。新集成请使用 `print text/qr` 或 JSON 输入。

打印和走纸命令会连接打印机、查询型号并检查就绪状态。`status` 只查询状态；`capabilities` 和帮助命令不连接设备。设备未就绪时，打印和走纸会停止。

**故障记录（2026-10-12）：先前长距离进退后曾报告 `0x02: paper jam`；手工收紧碳带并按 Feed/Pause 键后恢复就绪。`SET TEAR ON` 加单行回退的试印未能回到目标行。当前改为明确配置 `return_rows` 和 `present_rows`，下述两行抵消模型仍待本机连续不同列实测；通过前不要用于生产。**

## 1. 查看帮助

```sh
.venv/bin/python print_positions.py --help
.venv/bin/python print_positions.py print --help
.venv/bin/python print_positions.py feed --help
.venv/bin/python print_positions.py back --help
```

各处均可用 `-h` 代替 `--help`。这些帮助命令不会连接打印机或走纸。

## 2. 打印：print

语法：

```sh
.venv/bin/python print_positions.py print TEXT [--size {1,2,3,4,5}] [--pos COLUMN]
```

参数说明：

- `TEXT`：必填，打印内容。用 `/` 分隔相邻列的内容；建议始终用引号包围。
- `--size`：可选，选择内置字体编号 1～5，默认 2；不是毫米尺寸或字号倍数。
- `--pos`：可选，起始列，范围 1～8，默认 1。
- `--next`：已停用，传入此参数会在连接打印机之前报错。需要显式走纸时使用独立的 `feed` 命令。

### 单列打印

在当前纸张定位基础上，向第 1 列打印：

```sh
.venv/bin/python print_positions.py print "zz" --size 1 --pos 1
```

先显式前进一行，再向第 7 列打印：

```sh
.venv/bin/python print_positions.py feed 1
.venv/bin/python print_positions.py print "zz" --size 1 --pos 7
```

### 同一行多列打印

从第 1 列开始打印 8 个值，只发送一次打印作业：

```sh
.venv/bin/python print_positions.py print "1/2/3/4/5/6/7/8" --size 1
```

从第 3 列开始，依次在第 3、4、5 列打印：

```sh
.venv/bin/python print_positions.py print "A/B/C" --size 2 --pos 3
```

空字段跳过对应列。下面在第 1、3 列打印，第 2 列不绘制内容：

```sh
.venv/bin/python print_positions.py print "A//C" --size 2 --pos 1
```

空字段仍占用列位置，包括开头或末尾的空字段。起始列加字段数量不能超出第 8 列，否则退出并报错。

### 字体与内容限制

当前代码使用的单字符宽 × 高（单位：点）：

- `--size 1`：8 × 12。
- `--size 2`：12 × 20。
- `--size 3`：16 × 24。
- `--size 4`：24 × 32。
- `--size 5`：32 × 48。

每格为 60 × 60 点。脚本按上述尺寸计算居中位置；超长文本不会自动缩小、换行或按单格裁切，可能越过标签边界。字体 5 在单格中仅容得下一个上述宽度的字符。

当前没有中文字体或编码支持配置，请使用简单英文、数字等 ASCII 文本。`/` 固定用作分列符，没有打印字面斜杠的命令行选项。不要向文本传入换行符或原始打印机控制指令。

### 打印走纸行为

- 每个作业固定发送 `SET TEAR OFF`，关闭固件隐式撕取定位，避免其改变作业间的纸张基准。
- `PRINT` 后 `present_rows` 控制额外展示前进，默认 5 行。要在同一横向行打印另一个标签格，初次作业使用 `--return-rows 0`；之后按上一作业的 `present_rows + 1` 估算回退量，默认展示 5 行时估算为 `--return-rows 6`。该定位方式仍待实机确认。
- `--return-rows` 显式描述调用方的纸张状态，不保存跨 CLI 进程的内部计数。手工按 FEED、手工撕/拉纸或更换纸卷后，调用方应重新建立基准并从 `--return-rows 0` 开始。
- `--next` 已停用；新接口用 `--return-rows` 表示纸张定位。
- 脚本不跟踪物理行号；调用方需根据上次作业的展示量显式传入 `--return-rows`。同一行不同格可分别提交作业，但两行补偿模型仍待实机确认，验证前不要用于生产定位。
- 当前普通打印不会发送 `GAPDETECT` 或 `HOME`，也不会自动修复失准的纸张定位。

## 3. 前进：feed

```sh
.venv/bin/python print_positions.py feed [ROWS]
```

`ROWS` 为可选整数，默认 1，代码允许范围为 0～119。每行前进 84 点（7 mm），不打印文本。`0` 是不连接设备、不发送命令的空操作。可用 `--speed` 调整速度。

```sh
# 前进 1 行
.venv/bin/python print_positions.py feed

# 前进 2 行，即 14 mm
.venv/bin/python print_positions.py feed 2
```

实际发送 `FEED ROWS×84`。TSPL 的 `FEED` 参数单位是点，不是标签张数；命令行的 `ROWS` 才是本脚本定义的行数。

## 4. 回退：back

```sh
.venv/bin/python print_positions.py back [ROWS]
```

`ROWS` 为可选整数，默认 1，代码允许范围同样为 0～119。`0` 是不连接设备、不发送命令的空操作。可用 `--speed` 调整速度。

```sh
# 回退 1 行，即 7 mm
.venv/bin/python print_positions.py back
```

实际发送 `BACKFEED ROWS×84`。这是相对位移，不是“撤销上一次打印”，不会消除已经印出的文字，也不保证重新对齐之前的行。普通打印流程用 `--return-rows` 显式控制这段位移。119 行是协议/参数上限，不是安全回退距离；应保持回退步数尽量小，必要时降低速度。

**允许的数值上限不是安全回退距离。** 长距离回退可能造成纸张或碳带松弛、褶皱、卡纸。只有纸路正常、确实需要回退时才小步操作；故障时不要靠反复回退复位。

`feed` 和 `back` 都会先发送 `CLS` 和所选 `SPEED`，但不会重新设置 `SIZE` 或 `GAP`，也不执行校准。手册中 `SET BACK` 是切刀后的标签回退设置，不是回退时的碳带同步选项，因此不再发送。

## 5. USB 探测及方框试印：probe_te344.py

```sh
.venv/bin/python probe_te344.py
```

此脚本没有实现命令行参数解析，会：

1. 打开目标 USB 设备，显示序列号、接口及端点。
2. 查询状态、打印机信息和型号。
3. 设置 `SIZE 5 mm, 5 mm`、`GAP 2 mm, 0 mm`。
4. 绘制 `BOX 4,4,28,28,2` 并执行一次 `PRINT 1`。
5. 再次读取状态。

**这不是只读状态查询，会真实打印并改变纸张尺寸设置。** 它使用单个 5 mm 宽的测试区域，而不是主脚本的 54 mm 整行区域。它也没有主脚本的型号匹配与就绪拦截逻辑。

不要执行 `probe_te344.py --help` 来查看帮助：该脚本忽略命令行参数，仍会运行试印流程。

## 6. 日志和错误含义

- `model reply: b'TE344'`：设备返回型号 TE344。
- `TX ... bytes`：已写入的命令字节数，随后列出发送内容；不等于实物打印成功。
- `status 0x00: normal`：设备报告正常。
- `status 0x20: printing`：设备忙碌，走纸时也可能出现；不代表已印出文字。
- `done`：脚本完成当前流程，不是纸张位置或印刷质量的实物验收。

当前脚本识别的其他状态：

- `0x01`：打印头打开。
- `0x02`：卡纸/走纸检测异常。
- `0x03`：卡纸且打印头打开。
- `0x04`：缺纸。
- `0x05`：缺纸且打印头打开。
- `0x08`：缺碳带。
- `0x09`：缺碳带且打印头打开。
- `0x0A`：缺碳带且卡纸。
- `0x0B`：缺碳带、卡纸且打印头打开。
- `0x0C`：缺碳带且缺纸。
- `0x0D`：缺碳带、缺纸且打印头打开。
- `0x10`：暂停。
- `0x80`：其他错误。

常见退出信息：

- `TE344 not found`：未找到指定 USB 设备。
- `Printer did not answer model TE344`：型号回复不符合预期，不继续打印。
- `printer not ready: ...`：作业前检查未通过，未发送后续打印/走纸命令。
- `printer error: ...`：等待动作时检测到错误，作业可能已经部分执行。
- `printer did not run`：等待期间未确认完整的忙碌到就绪周期；不一定代表设备完全没动，不要盲目重发。
- `no reply`：没有收到状态回复。当前 `feed/back` 的动作后检查可能在无回复时仍输出 `done`，不能据此确认走纸成功。

出现 `paper jam` 时，先检查纸张、碳带和传感器位置。此状态也可能与间隙检测或定位失败有关，不能只凭状态码确定机械卡死。代码没有清错或复位命令。

## 7. 尚未提供的命令

当前提供 `print`、`status`、`capabilities`、`feed`、`back`。尚未提供 `calibrate`、`reset`、取消作业、份数、中文打印、自动选择下一个空槽或参数化纸张尺寸命令。

打印时固定发送的设置包含 `OFFSET 0 mm`、`LIMITFEED 20 mm`、关闭撕纸/剥离/切刀、`DIRECTION 1`、`REFERENCE 0,0`、`SHIFT 0` 和 `DENSITY 12`；`SPEED` 由 `--speed` 或 JSON `speed` 设置，默认 1.5 IPS。

`GAPDETECT` 是会主动走纸的校准命令，`HOME` 是寻标定位命令，不应视为不走纸的普通设置。当前代码已从日常打印流程中移除两者。

协议依据：仓库内下载的 `TSPL_TSPL2_Programming_Manual_3.0.pdf`，TSC 官方手册的 `PRINT`、`BACKFEED & BACKUP`、`SET BACK`、`SET TEAR`、`SET RIBBON` 章节。手册定义 `PRINT` 的标签组/份数、`BACKFEED` 的反向点数，并警告不当回退距离会卡纸/起皱；`SET RIBBON` 不调碳带张力，`SET BACK` 不是碳带同步控制，`SET TEAR ON` 也不承诺回到相同横向行。当前 `PRINT + 展示 FEED + 同行 BACKFEED` 的两行抵消模型等待本机验证。
