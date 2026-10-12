# TE344 打印命令使用说明

本文对应当前项目的 `print_positions.py` 和 `probe_te344.py`，列出这两个脚本的全部命令行入口，不是打印机完整 TSPL 指令手册。

## 使用前提与纸张配置

在项目目录运行以下命令，使用已有虚拟环境 `.venv/bin/python`。脚本依赖 PyUSB 和可用的 USB 后端，通过 USB 直接连接打印机，不通过系统打印队列。

当前配置固定为：

- 设备：TE344，USB VID:PID 为 `1203:0273`。
- 分辨率：300 DPI，按 12 dots/mm 换算。
- 每行 8 列；每个标签按 5 × 5 mm 计算，列间距和行间隙均为 2 mm。
- 整行打印区域宽 54 mm、高 5 mm。
- 一行纸张节距为 7 mm，即 84 点；不是 5 mm。

除帮助命令外，`print_positions.py` 的命令会先连接打印机、查询型号并检查就绪状态。设备未就绪会停止，不继续发送走纸或打印作业。

**当前已出现过 `0x02: paper jam`。以下示例是使用说明，不表示故障已解除。故障状态下不要连续重试或长距离回退。**

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
.venv/bin/python print_positions.py print TEXT --size {1,2,3,4,5} [--pos COLUMN] [--next]
```

参数说明：

- `TEXT`：必填，打印内容。用 `/` 分隔相邻列的内容；建议始终用引号包围。
- `--size`：必填，选择内置字体编号 1～5，不是毫米尺寸或字号倍数。
- `--pos`：可选，起始列，范围 1～8，默认 1。
- `--next`：可选，在打印前额外前进一行，发送 `FEED 84`。

### 单列打印

在当前纸张定位基础上，向第 1 列打印：

```sh
.venv/bin/python print_positions.py print "zz" --size 1 --pos 1
```

先额外前进一行，再向第 7 列打印：

```sh
.venv/bin/python print_positions.py print "zz" --size 1 --pos 7 --next
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

### “当前行”和“下一行”的实际含义

- 不加 `--next`：只是不执行额外的 `FEED`，不代表打印期间纸张保持静止。
- 加 `--next`：先前进 84 点，再执行 `PRINT 1,1`；打印过程本身也会走纸。因此总走纸距离不能简单视为只有 7 mm。
- 脚本不跟踪物理行号，也不会自动回到上一次打印的位置。连续执行两个 `print` 不能保证在同一物理行补印；同一行的多个值应使用 `/` 在一个作业中发送。
- 当前普通打印不会发送 `GAPDETECT` 或 `HOME`，也不会自动修复失准的纸张定位。

## 3. 前进：feed

```sh
.venv/bin/python print_positions.py feed [ROWS]
```

`ROWS` 为可选整数，默认 1，代码允许范围为 1～119。每行前进 84 点（7 mm），不打印文本。

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

`ROWS` 为可选整数，默认 1，代码允许范围同样为 1～119。

```sh
# 回退 1 行，即 7 mm
.venv/bin/python print_positions.py back
```

实际发送 `BACKFEED ROWS×84`。这是相对位移，不是“撤销上一次打印”，不会消除已经印出的文字，也不保证重新对齐之前的行。

**允许的数值上限不是安全回退距离。** 长距离回退可能造成纸张或碳带松弛、褶皱、卡纸。只有纸路正常、确实需要回退时才小步操作；故障时不要靠反复回退复位。

`feed` 和 `back` 都会先发送 `CLS`、`SET BACK ON`、`SPEED 2`，但不会重新设置 `SIZE` 或 `GAP`，也不执行校准。

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

## 7. 当前没有提供的命令

主脚本只有 `print`、`feed`、`back` 三个子命令以及帮助入口；没有独立的 `status`、`calibrate`、`reset`、取消作业、份数、中文打印或参数化纸张尺寸命令。

打印时固定发送的设置包含 `OFFSET 0 mm`、`LIMITFEED 20 mm`、关闭撕纸/剥离/切刀、`DIRECTION 1`、`REFERENCE 0,0`、`SHIFT 0`、`SPEED 2`、`DENSITY 12`。这些设置目前不能通过命令行修改。

`GAPDETECT` 是会主动走纸的校准命令，`HOME` 是寻标定位命令，不应视为不走纸的普通设置。当前代码已从日常打印流程中移除两者。
