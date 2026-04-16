import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkfont
from pymodbus.client import ModbusSerialClient
import pandas as pd
from datetime import datetime
import threading
import time
import serial
import serial.tools.list_ports
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import traceback
import logging
import pymodbus
from matplotlib.ticker import MultipleLocator, AutoMinorLocator

print("pymodbus version:", pymodbus.__version__)

logging.basicConfig(
    filename="data_collection.log",
    level=logging.INFO,
    format="%(asctime)s - [%(threadName)s] - %(message)s"
)


class ModbusRTUReaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Modbus RTU Reader with Plot")
        self.client = None
        self.running = False
        self.saved = False
        self.num_addresses = 10
        self.window_destroyed = False
        self.data_log = []
        self.times = []
        self.data = [[] for _ in range(self.num_addresses)]
        self.peaks = [[] for _ in range(self.num_addresses)]
        self.valleys = [[] for _ in range(self.num_addresses)]
        self.data_lock = threading.Lock()

        self.default_font = tkfont.Font(family="Helvetica", size=14)

        style = ttk.Style()
        style.configure("TLabel", font=self.default_font)
        style.configure("TEntry", font=self.default_font)
        style.configure("TCombobox", font=self.default_font)
        style.configure("TButton", font=self.default_font)

        self.fig, self.ax = plt.subplots(figsize=(10.8, 7.2))
        self.canvas = None

        plt.rcParams.update({
            "font.size": 14,
            "axes.titlesize": 16,
            "axes.labelsize": 14,
            "xtick.labelsize": 13,
            "ytick.labelsize": 13,
            "legend.fontsize": 11,
            "font.family": "Arial"
        })

        self.data_text = tk.Text(self.root, height=5, font=tkfont.Font(family="Helvetica", size=16))
        self.create_ui()

        self.root.grid_rowconfigure(4, weight=1)
        self.root.grid_rowconfigure(5, weight=3)
        self.root.grid_columnconfigure(0, weight=1)

        self.ui_thread = None
        self.read_thread = None

    def create_ui(self):
        
        # Serial Settings
        frame_conn = ttk.LabelFrame(self.root, text="Serial Settings")
        frame_conn.grid(row=0, column=0, padx=5, pady=5, sticky="ew")

        ttk.Label(frame_conn, text="COM Port:").grid(row=0, column=0, padx=2, pady=2, sticky="w")
        self.com_ports = self.get_com_ports()
        self.com_combo = ttk.Combobox(frame_conn, values=self.com_ports, width=12, font=self.default_font)
        if self.com_ports:
            self.com_combo.set(self.com_ports[0])
        self.com_combo.grid(row=0, column=1, padx=2, pady=2, sticky="w")

        ttk.Label(frame_conn, text="Baud Rate:").grid(row=0, column=2, padx=2, pady=2, sticky="w")
        self.baud_combo = ttk.Combobox(frame_conn, values=["9600", "19200", "38400", "57600", "115200"], width=10, font=self.default_font)
        self.baud_combo.set("9600")
        self.baud_combo.grid(row=0, column=3, padx=2, pady=2, sticky="w")

        ttk.Label(frame_conn, text="Slave ID:").grid(row=0, column=4, padx=2, pady=2, sticky="w")
        self.slave_entry = ttk.Entry(frame_conn, width=5, font=self.default_font)
        self.slave_entry.insert(0, "1")
        self.slave_entry.grid(row=0, column=5, padx=2, pady=2, sticky="w")

        self.refresh_btn = ttk.Button(frame_conn, text="Refresh", command=self.refresh_com_ports)
        self.refresh_btn.grid(row=0, column=6, padx=2, pady=2, sticky="w")

        # Read Settings
        frame_read = ttk.LabelFrame(self.root, text="Read Settings")
        frame_read.grid(row=1, column=0, padx=5, pady=5, sticky="ew")

        self.addr_entries = []
        self.check_vars = []
        self.check_buttons = []

        big_font = tkfont.Font(family="Helvetica", size=14, weight="bold")

        # 10 个地址一行，内部三行布局
        for i in range(self.num_addresses):
            frame_group = ttk.LabelFrame(frame_read, text=f"Addr {i+1}", labelanchor="n")
            frame_group.grid(row=0, column=i, padx=6, pady=4, sticky="nw")

            # 第一行：Enable
            var = tk.BooleanVar(value=(i == 0))
            self.check_vars.append(var)

            cb = ttk.Checkbutton(frame_group, text="Enable", variable=var)
            cb.configure(style="Big.TCheckbutton")
            cb.grid(row=0, column=0, padx=2, pady=4, sticky="w")
            self.check_buttons.append(cb)

            # 第二行：Addr:
            ttk.Label(frame_group, text="Addr:", font=big_font).grid(
                row=1, column=0, padx=2, pady=2, sticky="w"
            )

            # 第三行：输入框
            addr_entry = ttk.Entry(frame_group, width=7, font=big_font)
            addr_entry.insert(0, str(i * 10))
            addr_entry.grid(row=2, column=0, padx=2, pady=2, sticky="w")
            self.addr_entries.append(addr_entry)
             

        # ===== 按钮 + 频率 + 自定义刻度线 一行 =====
        frame_controls = ttk.Frame(self.root)
        frame_controls.grid(row=2, column=0, padx=5, pady=5, sticky="ew")
        frame_controls.grid_columnconfigure(10, weight=1)

        # Connect
        self.connect_btn = ttk.Button(frame_controls, text="Connect", command=self.toggle_connect)
        self.connect_btn.grid(row=0, column=0, padx=5, pady=5)

        # Start Reading
        self.start_btn = ttk.Button(frame_controls, text="Start Reading", command=self.toggle_reading, state="disabled")
        self.start_btn.grid(row=0, column=1, padx=5, pady=5)

        # Freq
        ttk.Label(frame_controls, text="Freq (Hz):").grid(row=0, column=2, padx=5, pady=5)
        self.freq_entry = ttk.Entry(frame_controls, width=6, font=self.default_font)
        self.freq_entry.insert(0, "1")
        self.freq_entry.grid(row=0, column=3, padx=5, pady=5)

        # 自定义刻度线
        ttk.Label(frame_controls, text="Line1:").grid(row=0, column=4, padx=5)
        ttk.Label(frame_controls, text="Line2:").grid(row=0, column=6, padx=5)
        ttk.Label(frame_controls, text="Line3:").grid(row=0, column=8, padx=5)

        self.line_values = []
        for i, col in zip(range(3), [5, 7, 9]):
            entry = ttk.Entry(frame_controls, width=8, font=self.default_font)
            entry.insert(0, "")
            entry.grid(row=0, column=col, padx=5)
            self.line_values.append(entry)


        self.data_text.grid(row=4, column=0, padx=5, pady=5, sticky="nsew")

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.root)
        self.canvas.get_tk_widget().grid(row=5, column=0, padx=5, pady=5, sticky="nsew")

        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Value")
        self.ax.set_title("Modbus Data Plot")
        self.ax.grid(True)
        self.root.bind("<Configure>", self.resize_plot)

    def resize_plot(self, event):
        if self.canvas and not self.window_destroyed:
            try:
                width = self.canvas.get_tk_widget().winfo_width() / 100
                height = self.canvas.get_tk_widget().winfo_height() / 100
                self.fig.set_size_inches(max(3, width), max(2, height))
                self.canvas.draw_idle()
            except Exception as e:
                logging.error(f"Resize plot error: {str(e)}")

    def redraw_plot(self, text_output, plot_data):
        try:
            if self.window_destroyed or not self.canvas:
                return

            self.ax.clear()
            
            # ===== 绘制自定义刻度线 =====
            for idx, entry in enumerate(self.line_values):
                try:
                    val = float(entry.get())
                    # 画水平线
                    self.ax.axhline(val, color="gray", linestyle="--", linewidth=1.2)

                    # 显示标签
                    self.ax.text(
                        0, val, f"Line{idx+1}: {val}",
                        color="gray",
                        fontsize=16,
                        va="bottom",
                        ha="left",
                        bbox=dict(facecolor="white", edgecolor="gray", alpha=0.7, boxstyle="round,pad=0.2")
                    )
                except:
                    pass  # 输入为空或非法则跳过


            colors = ['blue', 'red', 'green', 'purple', 'orange', 'cyan', 'magenta', 'lime', 'pink', 'teal']
            max_time = 0

            for i, (valid_times, valid_data, peaks, valleys) in enumerate(plot_data):
                if valid_data and len(valid_times) == len(valid_data):
                    if valid_times:
                        max_time = max(max_time, valid_times[-1])

                    self.ax.plot(valid_times, valid_data, label=f"Address {i+1}", color=colors[i % len(colors)], linewidth=2.2)

                    # 波峰波谷
                    if peaks:
                        for t, v in peaks:
                            self.ax.scatter(t, v, color=colors[i % len(colors)], marker="^", s=90, zorder=5)
                            self.ax.text(t, v + 2, f"{v}", color=colors[i % len(colors)], ha="center", va="bottom", fontsize=16, fontweight='bold')
                    if valleys:
                        for t, v in valleys:
                            self.ax.scatter(t, v, color=colors[i % len(colors)], marker="v", s=90, zorder=5)
                            self.ax.text(t, v - 2, f"{v}", color=colors[i % len(colors)], ha="center", va="top", fontsize=16, fontweight='bold')

                    # 最新数据点
                    if valid_times and valid_data:
                        self.ax.scatter(valid_times[-1], valid_data[-1], color=colors[i % len(colors)], marker="o", s=75, zorder=6)
                                            # 最新数值标签（曲线末尾）
                        self.ax.text(
                            valid_times[-1] + 2,              # 往右偏移一点，避免挡住点
                            valid_data[-1],                   # Y 值
                            f"{valid_data[-1]}",              # 显示数值
                            color=colors[i % len(colors)],
                            fontsize=16,
                            fontweight='bold',
                            va="center",
                            ha="left",
                            bbox=dict(facecolor="white", edgecolor=colors[i % len(colors)], boxstyle="round,pad=0.2", alpha=0.8),
                            zorder=7
                        )


            # 图例
            if self.ax.get_legend_handles_labels()[0]:
                self.ax.legend(loc='upper left', fontsize=11, framealpha=0.9)

            # ==================== 时间轴刻度智能处理 ====================
            if max_time > 0:
                self.ax.set_xlim(0, max_time + 10)

                # 0–20 秒：每 1 秒
                if max_time <= 20:
                    self.ax.xaxis.set_major_locator(MultipleLocator(1))
                    self.ax.xaxis.set_minor_locator(MultipleLocator(0.5))

                # 20–240 秒：每 10 秒
                elif max_time <= 240:
                    self.ax.xaxis.set_major_locator(MultipleLocator(10))
                    self.ax.xaxis.set_minor_locator(MultipleLocator(2))

                # 240–600 秒：每 30 秒
                elif max_time <= 600:
                    self.ax.xaxis.set_major_locator(MultipleLocator(30))
                    self.ax.xaxis.set_minor_locator(MultipleLocator(5))

                # 超过 10 分钟：每 60 秒
                else:
                    self.ax.xaxis.set_major_locator(MultipleLocator(60))
                    self.ax.xaxis.set_minor_locator(MultipleLocator(10))

            else:
                # 没有数据时固定显示 0~60 秒
                self.ax.set_xlim(0, 60)
                self.ax.xaxis.set_major_locator(MultipleLocator(10))

            # 强制显示刻度数值
            self.ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x)}"))

            self.ax.set_xlabel("Time (s)")
            self.ax.set_ylabel("Value")
            self.ax.set_title("Modbus Data Plot", pad=18)

            self.ax.grid(True, which='major', linestyle='--', alpha=0.8)
            self.ax.grid(True, which='minor', linestyle=':', alpha=0.5)

            self.canvas.draw_idle()

            # 更新下方文字区
            if self.data_text and not self.window_destroyed:
                self.data_text.delete(1.0, tk.END)
                for line in text_output:
                    self.data_text.insert(tk.END, line)

        except Exception as e:
            logging.error(f"Redraw plot error: {str(e)}\n{traceback.format_exc()}")
            if self.data_text and not self.window_destroyed:
                self.data_text.delete(1.0, tk.END)
                self.data_text.insert(tk.END, f"Plot update error: {str(e)}\n")

    def update_ui(self):
        update_interval = 3
        while self.running and not self.window_destroyed:
            try:
                with self.data_lock:
                    times = self.times.copy()
                    data = [d.copy() for d in self.data]
                    peaks = [p.copy() for p in self.peaks]
                    valleys = [v.copy() for v in self.valleys]

                plot_data = []
                for i in range(self.num_addresses):
                    if self.check_vars[i].get():
                        valid_times = [t for t, d in zip(times, data[i]) if d is not None]
                        valid_data = [d for d in data[i] if d is not None]
                        plot_data.append((valid_times, valid_data, peaks[i], valleys[i]))
                    else:
                        plot_data.append(([], [], [], []))

                if times:
                    timestamp = datetime.fromtimestamp(times[-1] + self.start_time)
                    text_output = [f"Time: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"]
                    for i in range(self.num_addresses):
                        if self.check_vars[i].get() and data[i] and data[i][-1] is not None:
                            text_output.append(f"Address {i+1}: [{data[i][-1]}]\n")
                else:
                    text_output = ["No data yet.\n"]

                if not self.window_destroyed:
                    self.root.after(0, self.redraw_plot, text_output, plot_data)

            except Exception as e:
                logging.error(f"Update UI error: {str(e)}\n{traceback.format_exc()}")
                self.running = False
                if not self.window_destroyed:
                    self.start_btn.config(text="Start Reading")
                break
            time.sleep(update_interval)

    # ==================== 下面方法保持不变（请从你之前的版本复制粘贴） ====================
    # get_com_ports, refresh_com_ports, toggle_connect, toggle_reading, find_peaks_and_valleys, read_modbus, save_data_and_plot, on_closing

    def get_com_ports(self):
        try:
            debug_info = ["--- Serial Port Detection ---\n"]
            debug_info.append(f"pyserial version: {serial.__version__}\n")
            ports = serial.tools.list_ports.comports(include_links=True)
            port_list = []
            if not ports:
                debug_info.append("No serial ports found.\n")
            else:
                debug_info.append(f"Found {len(ports)} serial ports:\n")
                for port in ports:
                    debug_info.append(f"Port: {port.device}\n")
                    debug_info.append(f" Description: {port.description}\n")
                    debug_info.append(f" HWID: {port.hwid}\n")
                    debug_info.append(f" Manufacturer: {port.manufacturer}\n")
                    debug_info.append(f" Product: {port.product}\n")
                    debug_info.append(f" Interface: {port.interface}\n")
                    try:
                        ser = serial.Serial(port.device, timeout=1)
                        ser.close()
                        debug_info.append(f" Status: Port is accessible\n")
                        port_list.append(port.device)
                    except serial.SerialException as se:
                        debug_info.append(f" Status: Port is not accessible - {str(se)}\n")
            debug_info.append("--- End of Detection ---\n")
            if self.data_text and not self.window_destroyed:
                self.data_text.delete(1.0, tk.END)
                self.data_text.insert(tk.END, "".join(debug_info))
            return port_list
        except Exception as e:
            error_msg = f"Error detecting serial ports: {str(e)}\n{traceback.format_exc()}\n"
            if self.data_text and not self.window_destroyed:
                self.data_text.delete(1.0, tk.END)
                self.data_text.insert(tk.END, error_msg)
            return []

    def refresh_com_ports(self):
        self.com_ports = self.get_com_ports()
        self.com_combo['values'] = self.com_ports
        if self.com_ports:
            self.com_combo.set(self.com_ports[0])
        else:
            self.com_combo.set("No ports available")
            messagebox.showwarning("Warning", "No serial ports found.")

    def toggle_connect(self):
        if self.client is None:
            if not self.com_ports:
                messagebox.showerror("Error", "No serial ports available.")
                return
            try:
                port = self.com_combo.get()
                baudrate = int(self.baud_combo.get())
                self.client = ModbusSerialClient(
                    port=port, baudrate=baudrate, parity="N", stopbits=1, bytesize=8, timeout=1
                )
                if self.client.connect():
                    self.connect_btn.config(text="Disconnect")
                    self.start_btn.config(state="normal")
                    self.data_text.delete(1.0, tk.END)
                    self.data_text.insert(tk.END, f"Connected to {port}, baudrate {baudrate}\n")
                else:
                    self.client.close()
                    self.client = None
                    self.start_btn.config(state="disabled")
                    self.data_text.delete(1.0, tk.END)
                    self.data_text.insert(tk.END, "Failed to connect.\n")
            except Exception as e:
                if self.client:
                    self.client.close()
                self.client = None
                self.start_btn.config(state="disabled")
                self.data_text.delete(1.0, tk.END)
                self.data_text.insert(tk.END, f"Connection error: {str(e)}\n")
        else:
            self.client.close()
            self.client = None
            self.connect_btn.config(text="Connect")
            self.start_btn.config(state="disabled")
            self.running = False
            self.start_btn.config(text="Start Reading")
            self.data_text.delete(1.0, tk.END)
            self.data_text.insert(tk.END, "Disconnected.\n")

    def toggle_reading(self):
        if not self.running:
            if self.client is None:
                messagebox.showerror("Error", "Please connect first.")
                return
            try:
                self.running = True
                self.start_btn.config(text="Stop Reading")
                self.saved = False
                with self.data_lock:
                    self.times = []
                    for i in range(self.num_addresses):
                        self.data[i] = []
                        self.peaks[i] = []
                        self.valleys[i] = []
                self.ax.clear()
                self.ax.set_title("Modbus Data Plot")
                self.ax.grid(True)
                self.canvas.draw_idle()

                self.read_thread = threading.Thread(target=self.read_modbus, daemon=True, name="ReadThread")
                self.read_thread.start()
                self.ui_thread = threading.Thread(target=self.update_ui, daemon=True, name="UIThread")
                self.ui_thread.start()
            except Exception as e:
                self.data_text.delete(1.0, tk.END)
                self.data_text.insert(tk.END, f"Start error: {str(e)}\n")
                self.running = False
                self.start_btn.config(text="Start Reading")
        else:
            self.running = False
            self.start_btn.config(text="Start Reading")
            if self.read_thread and self.read_thread.is_alive():
                self.read_thread.join(timeout=2.0)
            if self.ui_thread and self.ui_thread.is_alive():
                self.ui_thread.join(timeout=2.0)
            self.save_data_and_plot()

    def find_peaks_and_valleys(self, times, data, peaks_list, valleys_list):
        if len(data) < 3:
            return
        slopes = np.diff(data) / np.diff(times)
        change_points = [0]
        last_sign = 0
        for i in range(len(slopes)):
            curr_slope = slopes[i]
            curr_sign = 1 if curr_slope > 0 else (-1 if curr_slope < 0 else 0)
            if curr_sign != 0 and last_sign != curr_sign:
                change_points.append(i)
                last_sign = curr_sign
            elif curr_sign == 0 and last_sign != 0:
                change_points.append(i)
                last_sign = 0
            elif curr_sign != 0 and last_sign == 0:
                change_points.append(i)
                last_sign = curr_sign
        if change_points[-1] != len(data) - 1:
            change_points.append(len(data) - 1)

        for j in range(len(change_points) - 1):
            start_idx = change_points[j]
            end_idx = change_points[j + 1]
            segment_data = data[start_idx:end_idx + 1]
            segment_times = times[start_idx:end_idx + 1]

            if j > 0:
                prev_slope = slopes[start_idx - 1] if start_idx > 0 else 0
            else:
                prev_slope = 0
            if j < len(change_points) - 2:
                next_slope = slopes[end_idx] if end_idx < len(slopes) else 0
            else:
                next_slope = 0

            prev_sign = 1 if prev_slope > 0 else (-1 if prev_slope < 0 else 0)
            next_sign = 1 if next_slope > 0 else (-1 if next_slope < 0 else 0)

            if (prev_sign > 0 and next_sign < 0) or (prev_sign > 0 and next_sign == 0 and j < len(change_points) - 2 and slopes[change_points[j + 2] - 1] < 0):
                max_val = max(segment_data)
                max_indices = [i for i, val in enumerate(segment_data) if val == max_val]
                max_idx = max_indices[-1]
                peaks_list.append((segment_times[max_idx], segment_data[max_idx]))

            if (prev_sign < 0 and next_sign > 0) or (prev_sign < 0 and next_sign == 0 and j < len(change_points) - 2 and slopes[change_points[j + 2] - 1] > 0):
                min_val = min(segment_data)
                min_indices = [i for i, val in enumerate(segment_data) if val == min_val]
                min_idx = min_indices[-1]
                valleys_list.append((segment_times[min_idx], segment_data[min_idx]))

    def read_modbus(self):
        if self.client is None:
            return
        try:
            addrs = []
            for i in range(self.num_addresses):
                if self.check_vars[i].get():
                    addr = int(self.addr_entries[i].get())
                    addrs.append(addr)
                else:
                    addrs.append(None)

            freq = float(self.freq_entry.get())
            slave_id = int(self.slave_entry.get())
            interval = 1 / freq
            self.start_time = time.time()
        except:
            self.running = False
            return

        while self.running and self.client:
            try:
                loop_start = time.perf_counter()
                current_time = time.time()
                timestamp = datetime.now()
                elapsed_time = current_time - self.start_time

                data_row = [timestamp, elapsed_time]

                if not self.data_lock.acquire(timeout=1.0):
                    time.sleep(0.1)
                    continue

                try:
                    for i in range(self.num_addresses):
                        if self.check_vars[i].get():
                            try:
                                result = self.client.read_holding_registers(
                                    address=addrs[i], count=1, device_id=slave_id
                                )
                                if not result.isError():
                                    data = [int(reg) for reg in result.registers]
                                    data_row.extend(data)
                                    self.data[i].append(data[0] if data else None)
                                else:
                                    data_row.extend([None])
                                    self.data[i].append(None)
                            except:
                                data_row.extend([None])
                                self.data[i].append(None)
                        else:
                            data_row.extend([None])
                            self.data[i].append(None)

                    self.data_log.append(data_row)
                    self.times.append(elapsed_time)

                    for i in range(self.num_addresses):
                        if self.check_vars[i].get():
                            self.peaks[i] = []
                            self.valleys[i] = []
                            valid_times = [t for t, d in zip(self.times, self.data[i]) if d is not None]
                            valid_data = [d for d in self.data[i] if d is not None]
                            self.find_peaks_and_valleys(valid_times, valid_data, self.peaks[i], self.valleys[i])
                finally:
                    self.data_lock.release()

                loop_end = time.perf_counter()
                sleep_time = max(0, interval - (loop_end - loop_start))
                time.sleep(sleep_time)
            except Exception as e:
                logging.error(f"Read error: {e}")
                self.running = False
                break

        self.running = False
        if not self.window_destroyed:
            self.start_btn.config(text="Start Reading")

    def save_data_and_plot(self):
        if not self.data_log or self.saved:
            return
        try:
            self.saved = True
            columns = ["Timestamp", "ElapsedTime"]
            for i in range(self.num_addresses):
                if self.check_vars[i].get():
                    columns.append(f"Addr{i+1}_Reg1")
                else:
                    columns.append(f"Addr{i+1}_Disabled")

            df = pd.DataFrame(self.data_log, columns=columns)
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
            df["ElapsedTime"] = pd.to_numeric(df["ElapsedTime"], errors='coerce').round(0).astype('Int64')
            df = df.loc[:, df.notna().any(axis=0)]

            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            df.to_csv(f"modbus_rtu_data_{ts}.csv", index=False)
            self.fig.savefig(f"modbus_rtu_plot_{ts}.png", dpi=300, bbox_inches='tight')

            csv_name = f"modbus_rtu_data_{ts}.csv"
            png_name = f"modbus_rtu_plot_{ts}.png"

            self.data_text.delete(1.0, tk.END)
            self.data_text.insert(tk.END, "✅ 保存成功！\n")
            self.data_text.insert(tk.END, f"CSV 文件：{csv_name}\n")
            self.data_text.insert(tk.END, f"图片文件：{png_name}\n")
            self.data_text.insert(tk.END, "（文件已保存在当前程序目录）\n")

        except Exception as e:
            self.data_text.delete(1.0, tk.END)
            self.data_text.insert(tk.END, f"Save error: {str(e)}\n")
        finally:
            self.data_log = []
            self.running = False
            if not self.window_destroyed:
                self.start_btn.config(text="Start Reading")

    def on_closing(self):
        self.running = False
        self.window_destroyed = True
        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join(timeout=2.0)
        if self.ui_thread and self.ui_thread.is_alive():
            self.ui_thread.join(timeout=2.0)
        if self.client:
            self.client.close()
        plt.close(self.fig)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    root.geometry("1420x1150")
    app = ModbusRTUReaderApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()