# TE344 打印机 CLI

[English](README.md) | 简体中文

项目将应用内容生成、介质布局、TSPL 编码和 USB 执行分层。通过 USB 直接连接打印机，不经过操作系统打印队列。

## 环境与介质

使用已有虚拟环境 `.venv/bin/python`。依赖见 `requirements.txt`：Pillow、`qrcode` 和 PyUSB。默认 `media.default.json` 配置为 300 DPI（12 dots/mm），每行 8 个 5 × 5 mm 标签，横向与纵向间距均为 2 mm；行节距为 7 mm / 84 点。`print`、`render`、`feed`、`back` 和 `capabilities` 均可通过 `--media-config PATH` 指定其他配置。

## 命令

```sh
# 离线生成二维码行的 TSPL 文件和预览图，不连接 USB
.venv/bin/python print_positions.py render qr --data Q1 --data Q2 --slot 1 \
  --output /tmp/qr.tspl --preview /tmp/qr.png

# 将已生成的作业文件作为不透明字节发送给打印机
.venv/bin/python print_positions.py send /tmp/qr.tspl

# 便捷命令：生成并立即执行一整行 8 个二维码
.venv/bin/python print_positions.py print qr \
  --data Q1 --data Q2 --data Q3 --data Q4 \
  --data Q5 --data Q6 --data Q7 --data Q8 \
  --slot 1 --module-size 1 --ecc M --present-rows 0

# 从图片文件或 stdin 中的 Base64 生成 TSPL 作业
.venv/bin/python print_positions.py render image --file ./logo.png --slot 1 \
  --output /tmp/logo.tspl --preview /tmp/logo-preview.png
base64 < ./logo.png | .venv/bin/python print_positions.py render image \
  --base64-stdin --slot 1 --output /tmp/logo.tspl

# 文本、状态、设备/介质能力，以及逐行走纸
.venv/bin/python print_positions.py print text --data A123 --slot 1 --present-rows 0
.venv/bin/python print_positions.py status --json
.venv/bin/python print_positions.py capabilities
.venv/bin/python print_positions.py feed 5
.venv/bin/python print_positions.py feed 5 --continuous
.venv/bin/python print_positions.py back 3
```

## 内容与布局

- `print text`、`print qr` 和 `print image` 会创建一行作业并立即执行。`render` 生成二进制 TSPL 打印载荷但不访问 USB；指定 `--preview` 还会保存栅格行的 PNG 预览。`render` 不包含展示/回退走纸，`send` 只原样传输文件字节。TSPL 文件只能用于兼容的 TSPL 打印机和匹配的介质配置。
- 文本和二维码由应用层生成单色栅格；图片由应用层解码并二值化。TSPL 后端只接收最终整行位图，并编码成 `BITMAP` 指令，不使用打印机固件的文字或 `QRCODE` 命令。
- 重复传入 `--data` 可从 `--slot` 起连续放置二维码。`--module-size` 单模块宽度为 1～10 点；`--ecc` 支持 `L/M/Q/H`。5 mm 标签适合短内容，正式使用前需用目标扫码器验证。
- 图片输入必须二选一：`--file PATH` 或 `--base64-stdin`。`--fit contain` 保持比例并在标签内居中留白；`stretch` 拉伸铺满。`--threshold` 范围是 0～255。图片文件最大 30 MB，解码像素数最多 20 MP。
- `--slot` 从 1 开始。`--present-rows` 控制打印后的额外走纸，默认 5；设为 `0` 则不额外走纸。`--return-rows` 控制打印前回退，默认 0。同行回退定位未经实机验证，不要假定某个回退行数可以准确回到先前标签。
- 文本仅接受可打印 ASCII，由 Pillow 栅格化。`--font`（旧参数 `--size`）选择应用层字号 1～5，不是打印机字体编号。为兼容旧人工流程，仍保留 `print TEXT --size N --pos N` 及用 `/` 分隔列内容的语法。
- `feed [N]` 和 `back [N]` 支持 1～100 行，省略数量时默认为 1。默认逐行发送，并等待状态正常后再发送下一行；例如 `feed 5` 会依次发送 5 条独立的 `FEED 84`。只有 `feed` 支持 `--continuous`，例如 `feed 5 --continuous` 会一次发送 `FEED 420`。状态正常不能证明纸实际移动；进退纸是相对移动，不是撤销或对位功能。
- `--speed` 支持 `1, 1.5, 2, 2.5, 3, 3.5, 4, 5` IPS。`capabilities` 可校验并显示介质几何参数，不连接打印机。

## JSON 请求

应用可以将一个请求通过 stdin 传入：

```sh
printf '%s' '{"type":"qr","data":["Q1","Q2"],"slot":1,"present_rows":0}' \
  | .venv/bin/python print_positions.py print --json -
```

JSON 的 `type` 为 `text`、`qr`、`image` 或 `row`；图片使用 `image_base64`。`row` 使用 `placements` 数组，每项指定 `type`（`text` 或 `qr`）、`slot` 和 `data`，可在同一行、同一次打印中混排文字和二维码。可选全局字段包括 `font`、`ecc`、`module_size`、`fit`、`threshold`、`speed`、`present_rows` 和 `return_rows`。未知字段会被拒绝。`print --json -` 输出机器可读结果；`physical_output_verified:false` 表示软件不能验证纸面可见效果。`send --json` 也可输出 JSON。

## 分层结构

- `printer/application/content.py`、`printer/application/jobs.py`、`printer/application/pipeline.py`：内容、作业模型和应用层流水线。
- `printer/media/profile.py`、`printer/media/layout.py`：介质几何和独立于打印机的整行栅格布局。
- `printer/backend/tspl.py`：将行位图和走纸动作编码为 TSPL。
- `printer/execution/usb.py`：对不透明作业字节进行 USB 传输及状态检查。
- `printer/cli.py`：命令行编排；`print_positions.py` 保留为兼容入口。

实际实现已按层放入 `printer/`：应用层在 `printer/application/`，介质几何和布局在 `printer/media/`，TSPL 编码在 `printer/backend/`，USB 执行在 `printer/execution/`，命令行编排在 `printer/cli.py`。根目录的 `printer_cli.py` 和 `print_positions.py` 只是兼容入口。测试位于 `tests/`：

```sh
.venv/bin/python -m unittest discover -s tests -q
```

`probe_te344.py` 是独立诊断脚本，会实际试印并改变打印机设置，不是只读探测。

## 实机验证状态

TE344 之前已用原生二维码指令实物打印过一整行二维码。当前应用层栅格化打印链路尚未实机验证；扫码可靠性、上下居中，以及打印与展示走纸之间的关系仍需实际检查。此前长距离回退报告过卡纸状态；遇到位移不确定时，重试前先检查纸路和碳带。USB 写入后发生错误时，打印机可能已经执行作业，不要盲目重发。
