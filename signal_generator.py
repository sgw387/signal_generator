#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时音频信号发生器
====================

通过电脑声卡实时播放正弦波音频，支持两种模式：

  1. 单频模式（single）
     播放用户指定的单一频率纯正弦波，例如 50 Hz。

  2. 扫频模式（sweep）
     从起始频率连续扫到终止频率，每变化 0.1 Hz 更新一次频率，
     实现平滑连续的扫频效果（例如 50 Hz -> 100 Hz）。

功能特点：
  - 相位连续累加，切换频率时不会产生“咔哒”爆音；
  - 播放过程中可随时按「回车」或 Ctrl+C 停止；
  - 支持命令行、交互式、图形界面（tkinter）三种输入方式。

依赖安装
--------
  pip install numpy sounddevice

  说明：
  - sounddevice 底层使用 PortAudio。在 Windows / macOS 上，pip 安装时会
    自动附带编译好的 PortAudio 库，通常无需额外步骤。
  - 在 Linux 上如遇“找不到 PortAudio”错误，请先安装系统库：
        Ubuntu/Debian : sudo apt-get install libportaudio2
        Fedora        : sudo dnf install portaudio
        Arch          : sudo pacman -S portaudio

运行环境要求
------------
  - Python 3.8 及以上（推荐 3.9+）
  - 具有声卡或虚拟音频设备（Windows / macOS / Linux 均可）
  - 需要 numpy、sounddevice 两个第三方库
"""

import argparse
import sys
import threading
import time

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sys.exit("缺少依赖库 sounddevice，请先安装：\n    pip install numpy sounddevice")


EXAMPLES = """
示例：
  # 单频模式：播放 50 Hz 正弦波
  python signal_generator.py -m single -f 50

  # 扫频模式：50 -> 100 Hz（步长 0.1 Hz，速度 1 Hz/秒）
  python signal_generator.py -m sweep --start 50 --stop 100

  # 自定义扫频速度与幅度
  python signal_generator.py -m sweep --start 50 --stop 100 --rate 2 --amplitude 0.5

  # 交互式输入
  python signal_generator.py

  # 图形界面
  python signal_generator.py --gui

  # 列出音频设备
  python signal_generator.py -l
"""


class SignalGenerator:
    """基于声卡实时输出正弦波的信号发生器（线程安全）。"""

    def __init__(self, samplerate=44100, amplitude=0.3, channels=1):
        self.samplerate = int(samplerate)
        if self.samplerate <= 0:
            raise ValueError("采样率必须为正整数")

        self.channels = int(channels)
        if self.channels < 1:
            raise ValueError("声道数至少为 1")

        # 幅度（0~1），通过属性 setter 校验并夹取
        self._amplitude = 0.3
        self.amplitude = amplitude

        # 相位累加器（单位：弧度），跨数据块保持连续，
        # 从而在改变频率时避免相位跳变导致的“咔哒”声。
        self._phase = 0.0

        # 当前播放频率（Hz），由扫频线程动态更新，读写加锁保证线程安全。
        self._lock = threading.Lock()
        self._freq = 0.0

        # 停止事件：置位后播放与扫频线程都会尽快退出。
        self._stop_event = threading.Event()

        self._stream = None          # sounddevice 输出流
        self.sweep_thread = None     # 扫频线程引用（供 GUI 轮询状态）

    # ------------------------------------------------------------------
    # 属性：幅度 / 频率（带校验，线程安全）
    # ------------------------------------------------------------------
    @property
    def amplitude(self):
        return self._amplitude

    @amplitude.setter
    def amplitude(self, value):
        v = float(value)
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"幅度必须在 0~1 之间，收到 {value!r}")
        self._amplitude = v

    @property
    def frequency(self):
        with self._lock:
            return self._freq

    @frequency.setter
    def frequency(self, value):
        v = float(value)
        if v <= 0:
            raise ValueError(f"频率必须为正数，收到 {value!r}")
        with self._lock:
            self._freq = v

    # ------------------------------------------------------------------
    # 音频回调：PortAudio 每准备好一个数据块就调用一次
    # ------------------------------------------------------------------
    def _callback(self, outdata, frames, time_info, status):
        if status:
            print(f"[警告] 音频流状态异常：{status}", file=sys.stderr)

        freq = self.frequency
        n = frames

        # 逐样本累加相位：phase += 2π·f/fs。
        # 由于相位是连续变量，即使频率中途改变，波形也不会出现跳变，
        # 这正是“平滑扫频”的关键。
        dphase = 2.0 * np.pi * freq / self.samplerate
        phases = self._phase + dphase * np.arange(1, n + 1, dtype=np.float64)
        self._phase = phases[-1] % (2.0 * np.pi)

        # 生成正弦波并复制到所有声道（outdata 形状为 (frames, channels)）。
        wave = self.amplitude * np.sin(phases)
        outdata[:] = wave[:, np.newaxis]

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------
    def _start_stream(self):
        """创建并启动（非阻塞）输出流。"""
        if self._stream is not None:
            self.stop()

        self._phase = 0.0
        self._stream = sd.OutputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype="float32",
            callback=self._callback,
            blocksize=0,  # 0 表示交由 PortAudio 自动选择
        )
        self._stream.start()

    def stop(self):
        """停止播放与扫频（幂等，可安全地多次调用）。"""
        self._stop_event.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ------------------------------------------------------------------
    # 单频模式
    # ------------------------------------------------------------------
    def start_single(self, freq):
        """开始播放指定频率的正弦波（非阻塞）。"""
        self._stop_event.clear()
        self.frequency = freq
        self._start_stream()

    # ------------------------------------------------------------------
    # 扫频模式
    # ------------------------------------------------------------------
    def start_sweep(self, start, stop, step=0.1, rate_hz_per_sec=1.0):
        """
        开始扫频（非阻塞）。

        参数
        ----
        start / stop : 起始 / 终止频率（Hz），stop 可小于 start 实现降频扫描。
        step         : 每次变化的频率步长（Hz），默认 0.1。
        rate_hz_per_sec : 扫频速度，每秒变化多少 Hz，默认 1.0。
                          每步持续时长 = step / rate_hz_per_sec 秒。
        """
        step = abs(float(step))
        if step <= 0:
            raise ValueError("步长 step 必须大于 0")

        rate = abs(float(rate_hz_per_sec))
        if rate <= 0:
            raise ValueError("扫频速度 rate 必须大于 0")
        step_duration = step / rate

        direction = 1.0 if stop >= start else -1.0
        target = float(stop)

        self._stop_event.clear()
        self.frequency = start
        self._start_stream()

        def _sweep_loop():
            # 用单调时钟（perf_counter）计算已流逝时间，再反推当前应处的频率台阶。
            # 这样即使系统定时器精度较低（如 Windows 默认约 15.6ms），
            # 也不会产生累积漂移，扫频节奏始终准确、平滑。
            step_signed = step * direction
            t0 = time.perf_counter()
            while not self._stop_event.is_set():
                elapsed = time.perf_counter() - t0
                n_steps = int(elapsed / step_duration)
                cur = start + step_signed * n_steps
                cur = min(cur, target) if direction > 0 else max(cur, target)
                self.frequency = cur
                if cur == target:
                    break
                # 短暂轮询等待；轮询间隔的精度不影响扫频准确性
                self._stop_event.wait(0.005)
            if not self._stop_event.is_set():
                print(f"\n扫频完成，已到达 {target:g} Hz，保持该频率播放，按回车停止。")

        self.sweep_thread = threading.Thread(target=_sweep_loop, daemon=True)
        self.sweep_thread.start()


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def wait_for_stop(gen):
    """阻塞主线程直到用户请求停止：按回车或 Ctrl+C。"""
    print("正在播放…… 按「回车」或 Ctrl+C 停止。")
    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        gen.stop()


def list_devices():
    """列出可用音频设备。"""
    print("可用音频设备：")
    print(sd.query_devices())
    try:
        default_out = sd.query_devices(kind="output")
        print("\n默认输出设备：", default_out["name"])
    except Exception:
        pass


# ----------------------------------------------------------------------
# 交互模式
# ----------------------------------------------------------------------
def interactive_mode():
    print("=" * 52)
    print("  实时音频信号发生器")
    print("=" * 52)
    gen = SignalGenerator()
    while True:
        print("\n请选择播放模式：")
        print("  1) 单频模式 —— 播放指定频率的正弦波")
        print("  2) 扫频模式 —— 从起始频率连续扫到终止频率")
        print("  q) 退出")
        choice = input("请输入 [1/2/q]：").strip().lower()
        if choice in ("q", "quit", "exit"):
            print("再见。")
            break
        if choice == "1":
            try:
                f = float(input("请输入频率（Hz）："))
            except ValueError:
                print("输入无效，请输入数字。")
                continue
            try:
                gen.start_single(f)
            except ValueError as e:
                print(f"参数错误：{e}")
                continue
            print(f"开始播放 {f:g} Hz 正弦波。")
            wait_for_stop(gen)
        elif choice == "2":
            try:
                start = float(input("起始频率（Hz）："))
                stop = float(input("终止频率（Hz）："))
                step_s = input("频率步长（Hz，默认 0.1）：").strip()
                step = float(step_s) if step_s else 0.1
                rate_s = input("扫频速度（Hz/秒，默认 1.0）：").strip()
                rate = float(rate_s) if rate_s else 1.0
            except ValueError:
                print("输入无效，请输入数字。")
                continue
            try:
                gen.start_sweep(start, stop, step, rate)
            except ValueError as e:
                print(f"参数错误：{e}")
                continue
            print(f"开始扫频 {start:g} -> {stop:g} Hz。")
            wait_for_stop(gen)
        else:
            print("无效选项，请重新输入。")


# ----------------------------------------------------------------------
# 图形界面（tkinter，Python 标准库自带）
# ----------------------------------------------------------------------
def run_gui():
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        sys.exit("当前环境未包含 tkinter，无法启动图形界面，请改用命令行或交互模式。")

    gen = SignalGenerator()

    root = tk.Tk()
    root.title("实时音频信号发生器")
    root.resizable(False, False)

    pad = {"padx": 10, "pady": 6}
    frm = ttk.Frame(root, padding=12)
    frm.grid(sticky="nsew")

    # 模式选择
    mode_var = tk.StringVar(value="single")
    ttk.Label(frm, text="播放模式：").grid(row=0, column=0, sticky="w", **pad)
    ttk.Radiobutton(frm, text="单频", variable=mode_var, value="single").grid(row=0, column=1, sticky="w", **pad)
    ttk.Radiobutton(frm, text="扫频", variable=mode_var, value="sweep").grid(row=0, column=2, sticky="w", **pad)

    # 参数输入
    freq_var = tk.StringVar(value="440")
    start_var = tk.StringVar(value="50")
    stop_var = tk.StringVar(value="100")
    step_var = tk.StringVar(value="0.1")
    rate_var = tk.StringVar(value="1.0")
    amp_var = tk.StringVar(value="0.3")

    row = 1
    ttk.Label(frm, text="频率 (Hz)：").grid(row=row, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=freq_var, width=12).grid(row=row, column=1, sticky="w", **pad)

    row += 1
    ttk.Label(frm, text="起始频率 (Hz)：").grid(row=row, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=start_var, width=12).grid(row=row, column=1, sticky="w", **pad)

    row += 1
    ttk.Label(frm, text="终止频率 (Hz)：").grid(row=row, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=stop_var, width=12).grid(row=row, column=1, sticky="w", **pad)

    row += 1
    ttk.Label(frm, text="步长 (Hz)：").grid(row=row, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=step_var, width=12).grid(row=row, column=1, sticky="w", **pad)

    row += 1
    ttk.Label(frm, text="速度 (Hz/秒)：").grid(row=row, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=rate_var, width=12).grid(row=row, column=1, sticky="w", **pad)

    row += 1
    ttk.Label(frm, text="幅度 (0~1)：").grid(row=row, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=amp_var, width=12).grid(row=row, column=1, sticky="w", **pad)

    status_var = tk.StringVar(value="就绪")
    row += 1
    tk.Label(frm, textvariable=status_var, fg="#2e9e5b", anchor="w").grid(
        row=row, column=0, columnspan=3, sticky="w", **pad
    )

    def _poll_sweep():
        """周期性检查扫频线程是否结束，结束后更新状态栏。"""
        t = gen.sweep_thread
        if t is not None and not t.is_alive():
            status_var.set(f"扫频完成，保持 {gen.frequency:g} Hz 播放")
            gen.sweep_thread = None
            return
        if t is not None:
            root.after(200, _poll_sweep)

    def _set_amplitude():
        try:
            gen.amplitude = float(amp_var.get())
            return True
        except ValueError as e:
            status_var.set(str(e))
            return False

    def on_start():
        if not _set_amplitude():
            return
        try:
            if mode_var.get() == "single":
                f = float(freq_var.get())
                gen.start_single(f)
                status_var.set(f"正在播放 {f:g} Hz")
            else:
                s = float(start_var.get())
                t = float(stop_var.get())
                st = float(step_var.get())
                r = float(rate_var.get())
                gen.start_sweep(s, t, st, r)
                status_var.set(f"正在扫频 {s:g} -> {t:g} Hz")
                _poll_sweep()
        except ValueError as e:
            status_var.set(f"参数错误：{e}")

    def on_stop():
        gen.stop()
        status_var.set("已停止")

    btns = ttk.Frame(frm)
    btns.grid(row=row + 1, column=0, columnspan=3, sticky="w", **pad)
    ttk.Button(btns, text="开始", command=on_start).grid(row=0, column=0, padx=4)
    ttk.Button(btns, text="停止", command=on_stop).grid(row=0, column=1, padx=4)

    def on_close():
        gen.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# ----------------------------------------------------------------------
# 命令行入口
# ----------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="实时音频信号发生器：单频 / 扫频两种模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES,
    )
    parser.add_argument("-m", "--mode", choices=["single", "sweep"],
                        help="播放模式：single=单频，sweep=扫频；不指定则进入交互模式")
    parser.add_argument("-f", "--freq", type=float, help="单频模式的频率（Hz）")
    parser.add_argument("--start", type=float, help="扫频起始频率（Hz）")
    parser.add_argument("--stop", type=float, help="扫频终止频率（Hz）")
    parser.add_argument("--step", type=float, default=0.1, help="扫频步长（Hz），默认 0.1")
    parser.add_argument("--rate", type=float, default=1.0, help="扫频速度（Hz/秒），默认 1.0")
    parser.add_argument("--amplitude", type=float, default=0.3, help="幅度（0~1），默认 0.3")
    parser.add_argument("--samplerate", type=int, default=44100, help="采样率，默认 44100")
    parser.add_argument("-l", "--list-devices", action="store_true", help="列出可用音频设备后退出")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    args = parser.parse_args(argv)

    if args.list_devices:
        list_devices()
        return 0

    if args.gui:
        run_gui()
        return 0

    try:
        gen = SignalGenerator(samplerate=args.samplerate, amplitude=args.amplitude)

        if args.mode == "single":
            if args.freq is None:
                parser.error("单频模式需要 -f/--freq 指定频率")
            gen.start_single(args.freq)
            print(f"开始播放 {args.freq:g} Hz 正弦波。")
            wait_for_stop(gen)
        elif args.mode == "sweep":
            if args.start is None or args.stop is None:
                parser.error("扫频模式需要 --start 与 --stop 指定频率范围")
            gen.start_sweep(args.start, args.stop, args.step, args.rate)
            print(f"开始扫频 {args.start:g} -> {args.stop:g} Hz。")
            wait_for_stop(gen)
        else:
            interactive_mode()
    except ValueError as e:
        print(f"[参数错误] {e}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已停止。")
        sys.exit(0)
