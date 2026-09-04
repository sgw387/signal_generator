# 实时音频信号发生器

> 通过电脑声卡实时播放正弦波信号，支持单频播放与连续扫频两种模式。

- **文件**：`signal_generator.py`
- **语言**：Python 3.8+（推荐 3.9+）
- **依赖**：`numpy`、`sounddevice`（含 PortAudio）
- **可选依赖**：tkinter（标准库，图形界面需要；Linux 用户可能需要额外安装 `python3-tk`）

---

## 功能特性

| 模式       | 说明                                                       |
| ---------- | ---------------------------------------------------------- |
| 单频模式   | 播放指定频率的纯正弦波，例如 50 Hz。                       |
| 扫频模式   | 从起始频率连续平滑地扫到终止频率，步长 0.1 Hz（可调）。   |
| 三种输入   | 命令行（argparse）、交互式、tkinter 图形界面，任选其一。   |
| 平滑无爆音 | 相位累加器保证变频时波形连续，切换无「咔哒」声。           |
| 无累积漂移 | 扫频节奏由单调时钟反推，跨平台始终准确（Windows 也无漂移）。 |
| 随时停止   | 播放中按「回车」或 `Ctrl+C` 立即停止。                     |

---

## 安装

```bash
pip install numpy sounddevice
```

`sounddevice` 底层使用 PortAudio：

- **Windows / macOS**：pip 安装时会自动附带编译好的 PortAudio，无需额外步骤。
- **Linux**（Debian/Ubuntu）：如遇 `PortAudio library not found`，先装系统库：

  ```bash
  sudo apt-get install libportaudio2
  # Fedora : sudo dnf install portaudio
  # Arch   : sudo pacman -S portaudio
  ```

## 运行环境要求

- Python 3.8 及以上
- 具有声卡或虚拟音频设备（Windows / macOS / Linux 均可）
- 扬声器或耳机

---

## 三种使用方式

### 1. 命令行

```bash
# 单频模式：播放 50 Hz
python signal_generator.py -m single -f 50

# 扫频模式：50 -> 100 Hz，步长 0.1 Hz，速度 1 Hz/秒
python signal_generator.py -m sweep --start 50 --stop 100

# 自定义扫频速度与幅度
python signal_generator.py -m sweep --start 50 --stop 100 --rate 2 --amplitude 0.5

# 列出音频设备
python signal_generator.py -l
```

### 2. 交互式

不传任何参数运行：

```bash
python signal_generator.py
```

进入菜单后按 `1` / `2` 选择单频或扫频，按提示输入频率即可。`q` 退出。

### 3. 图形界面（tkinter）

```bash
python signal_generator.py --gui
```

弹出可视化窗口：选模式 → 填参数 → 「开始」/「停止」。

---

## 命令行参数

| 参数                | 说明                                              | 默认值 |
| ------------------- | ------------------------------------------------- | ------ |
| `-m`, `--mode`      | 播放模式：`single`（单频）或 `sweep`（扫频）。不指定则进入交互模式。 | —      |
| `-f`, `--freq`      | 单频模式的频率（Hz）。                            | —      |
| `--start`           | 扫频起始频率（Hz）。                              | —      |
| `--stop`            | 扫频终止频率（Hz），可小于起始频率实现降频扫描。  | —      |
| `--step`            | 扫频步长（Hz），每次变化的频率增量。              | `0.1`  |
| `--rate`            | 扫频速度（Hz/秒），即每秒变化多少 Hz。            | `1.0`  |
| `--amplitude`       | 幅度（0~1），越大越响。                            | `0.3`  |
| `--samplerate`      | 采样率（Hz）。                                    | `44100`|
| `-l`, `--list-devices` | 列出可用音频设备后退出。                       | —      |
| `--gui`             | 启动图形界面。                                    | —      |
| `-h`, `--help`      | 查看帮助信息。                                    | —      |

---

## 使用示例

```bash
# 1) 单频 50 Hz，按回车停止
python signal_generator.py -m single -f 50

# 2) 扫频 50 → 100 Hz，步长 0.1 Hz，速度 1 Hz/秒（约 50 秒）
python signal_generator.py -m sweep --start 50 --stop 100

# 3) 快速扫频：100 Hz/秒，20 Hz → 20 kHz
python signal_generator.py -m sweep --start 20 --end 20000 --rate 100

# 4) 降频扫描：1 kHz → 50 Hz
python signal_generator.py -m sweep --start 1000 --stop 50 --rate 5

# 5) 单频 A4 标准音（440 Hz），音量 0.5
python signal_generator.py -m single -f 440 --amplitude 0.5

# 6) 调音听音测试：1 kHz 持续 5 秒（可用定时器停止）
#    （本程序未提供 --duration，Ctrl+C 或回车停止）

# 7) 查看当前所有音频设备
python signal_generator.py -l
```

---

## 停止播放

任何模式下：

- **按「回车」**：立即停止播放并退出。
- **按 `Ctrl+C`**：效果等同「回车」。

**扫频到达终点后**：程序会保持终点频率继续播放（不会出现突然静音），并打印提示。按回车即可停止。

---

## 工作原理

### 相位累加器：换频无爆音

普通做法 `sin(2π·f·t)` 在切换 `f` 时会造成波形相位跳变，产生「咔哒」爆音。

本程序维护一个跨数据块**连续累加**的相位变量：

```
phase[i+1] = phase[i] + 2π · f / fs
output[i]  = A · sin(phase[i])
```

由于相位是连续变化的累计量，即使频率中途改变，波形也不会跳变。这是「平滑扫频」的核心。

### 无累积漂移的扫频

早期版本用 `Event.wait(step_duration)` 逐步累加计时，但 Windows 默认定时器精度约 15.6 ms，会让快速扫频节奏被拖慢、累积漂移。

当前实现改为：

```
t0 = time.perf_counter()                      # 单调时钟起点
cur = start + direction * step * n_steps
n_steps = int((perf_counter() - t0) / step_duration)
```

用已流逝的时间反推当前应处的频率台阶，**轮询间隔的精度不再影响扫频准确性**。这样无论 Windows 还是 Linux/macOS，扫频节奏都一致且无漂移。

### 线程模型

- **音频回调线程**（PortAudio 内部）：每个数据块（约 10~20 ms）调用一次，读取当前 `frequency` 并生成一段正弦波。
- **扫频线程**（仅扫频模式）：周期性计算并更新 `self.frequency`。
- **主线程**：等待用户输入（回车/Ctrl+C）后调用 `stop()`。

`frequency` 的读写通过 `threading.Lock` 保护，避免竞态。

---

## 常见问题

**Q：运行报 `缺少依赖库 sounddevice`？**
A：执行 `pip install numpy sounddevice`。

**Q：Linux 报 `PortAudio library not found`？**
A：先 `sudo apt-get install libportaudio2`（或对应发行版的安装命令），再 `pip install sounddevice`。

**Q：扫频到终点后想自动停止，不想按回车？**
A：当前版本扫频到达终点后会保持该频率继续播放（不突然静音），需手动回车停止。如需自动停止，可在 `Ctrl+C` 后再启动，或在交互模式中用 `q` 退出。

**Q：想限制音量、保护听力？**
A：用 `--amplitude 0.1` 之类的较小幅度。建议在耳机/音箱音量较大时，把程序内幅度调到 0.1~0.3 区间。

**Q：能输出其他波形（方波、三角波、白噪声）吗？**
A：当前仅支持正弦波。如果有需要，扩展 `SignalGenerator._callback` 即可。

**Q：能选择输出设备吗？**
A：可先用 `python signal_generator.py -l` 查看设备列表，然后在代码里通过 `sd.OutputStream(device=...)` 指定（当前 CLI 未暴露该参数）。

---

## 文件结构

```
D:\xhfsq\
├── signal_generator.py    # 主程序
└── readme.md              # 本文档
```

---

## 致谢

- `sounddevice`：基于 PortAudio 的 Python 音频流库。
- `numpy`：相位累加与向量化波形生成。
- `tkinter`：Python 内置 GUI 工具包。
