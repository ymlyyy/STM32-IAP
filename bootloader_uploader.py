# -*- coding: utf-8 -*-
"""
STM32 IAP Bootloader 上位机 v6
==============================

功能说明:
    - 手动模式: 选择bin文件，点击"一键下载"完成固件升级
    - 自动模式: 监听串口，检测到设备进入boot模式自动升级
    
通信协议:
    1. 握手: 发送 upgrade:"<文件大小>"
    2. 等待: 设备确认后擦除Flash，返回"进入接收数据状态"
    3. 传输: 按256字节分包发送，间隔20ms
    4. 完成: 设备返回"数据写入完成，长度正确: XXXX"

适用场景:
    - 开发测试: 改代码→编译→按复位→自动下载
    - 批量生产: 选择固件→一键下载

依赖: pyserial (pip install pyserial)
"""

import sys
import os
import time
import threading

# 检查tkinter是否可用
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    print("错误: 缺少tkinter，请安装完整Python（勾选tcl/tk组件）")
    sys.exit(1)

# 检查pyserial是否安装
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("错误: 缺少pyserial，请运行: pip install pyserial")
    sys.exit(1)


class BootloaderGUI:
    """
    STM32 IAP Bootloader 上位机主类
    
    属性:
        MAX_FIRMWARE_SIZE: 最大固件大小限制（54KB，对应bootloader分配的APP空间）
        CHUNK_SIZE: 数据分包大小（256字节，配合STM32空闲中断）
        CHUNK_DELAY: 分包发送间隔（20ms，让设备有时间处理）
    """
    
    # 固件大小限制：APP区域从0x08002800开始，大小54KB
    MAX_FIRMWARE_SIZE = 54 * 1024  # 54KB
    
    # 分包参数：256字节/包，20ms间隔（配合设备空闲中断）
    CHUNK_SIZE = 256
    CHUNK_DELAY = 0.02  # 20ms

    def __init__(self):
        """初始化上位机"""
        self.root = tk.Tk()
        self.root.title("STM32 IAP Bootloader 上位机")
        self.root.geometry("580x520")
        self.root.resizable(False, False)

        # 串口相关
        self.serial_port = None      # 串口对象
        self.is_connected = False    # 连接状态
        
        # 升级相关
        self.is_uploading = False    # 正在升级标志
        self.file_path = ""          # 固件文件路径
        
        # 自动模式相关
        self.auto_mode = False       # 自动模式开关
        self.auto_thread = None      # 自动检测线程
        self.stop_auto = False       # 停止自动检测标志

        # 创建界面并刷新串口列表
        self._build_ui()
        self._refresh_ports()

    # ============================================================
    # 界面构建
    # ============================================================
    
    def _build_ui(self):
        """
        创建GUI界面
        
        布局:
            - 串口设置区: 串口选择、波特率、连接/断开
            - 固件文件区: 文件路径、浏览按钮、文件信息
            - 操作区: 一键下载按钮、自动检测开关
            - 进度条: 显示传输进度
            - 日志区: 显示详细操作日志
            - 状态栏: 显示当前状态
        """
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # ── 串口设置区域 ──
        f1 = ttk.LabelFrame(main, text="串口设置", padding=8)
        f1.pack(fill=tk.X, pady=(0, 8))
        r1 = ttk.Frame(f1)
        r1.pack(fill=tk.X)

        # 串口选择下拉框
        ttk.Label(r1, text="串口:").pack(side=tk.LEFT)
        self.cb_port = ttk.Combobox(r1, width=12, state="readonly")
        self.cb_port.pack(side=tk.LEFT, padx=(2, 8))

        # 波特率选择下拉框（默认115200，与bootloader匹配）
        ttk.Label(r1, text="波特率:").pack(side=tk.LEFT)
        self.cb_baud = ttk.Combobox(r1, width=8, state="readonly")
        self.cb_baud['values'] = ('9600', '19200', '38400', '57600', '115200', '230400', '460800', '921600')
        self.cb_baud.set('115200')  # 默认波特率
        self.cb_baud.pack(side=tk.LEFT, padx=(2, 8))

        # 刷新和连接按钮
        ttk.Button(r1, text="刷新", command=self._refresh_ports).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_conn = ttk.Button(r1, text="连接", command=self._toggle_conn)
        self.btn_conn.pack(side=tk.LEFT)

        # ── 固件文件区域 ──
        f2 = ttk.LabelFrame(main, text="固件文件", padding=8)
        f2.pack(fill=tk.X, pady=(0, 8))
        r2 = ttk.Frame(f2)
        r2.pack(fill=tk.X)

        # 文件路径输入框
        self.entry_file = ttk.Entry(r2)
        self.entry_file.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        
        # 浏览按钮
        self.btn_browse = ttk.Button(r2, text="浏览", command=self._browse)
        self.btn_browse.pack(side=tk.LEFT)

        # 文件信息标签（显示大小等）
        self.lbl_info = ttk.Label(f2, text="未选择文件")
        self.lbl_info.pack(anchor=tk.W, pady=(4, 0))

        # ── 操作按钮区域 ──
        f3 = ttk.Frame(main)
        f3.pack(fill=tk.X, pady=(0, 8))

        # 一键下载按钮
        self.btn_upload = ttk.Button(f3, text="一键下载", command=self._start_upload)
        self.btn_upload.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_upload.state(['disabled'])  # 初始禁用，连接串口后启用

        # 自动检测模式开关
        self.var_auto = tk.BooleanVar(value=False)
        self.chk_auto = ttk.Checkbutton(f3, text="自动检测模式", 
                                          variable=self.var_auto,
                                          command=self._toggle_auto_mode)
        self.chk_auto.pack(side=tk.LEFT)
        self.chk_auto.state(['disabled'])  # 初始禁用，连接串口后启用

        # ── 进度条 ──
        self.progress = ttk.Progressbar(main, mode='determinate')
        self.progress.pack(fill=tk.X, pady=(0, 8))

        # ── 日志区域 ──
        f4 = ttk.LabelFrame(main, text="日志", padding=4)
        f4.pack(fill=tk.BOTH, expand=True)
        
        # 日志文本框（只读，等宽字体便于对齐）
        self.txt_log = tk.Text(f4, height=10, font=('Consolas', 9))
        sb = ttk.Scrollbar(f4, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        # ── 状态栏 ──
        self.var_status = tk.StringVar(value="就绪")
        ttk.Label(main, textvariable=self.var_status, relief=tk.SUNKEN,
                  anchor=tk.W, padding=3).pack(fill=tk.X, pady=(8, 0))

    # ============================================================
    # 辅助方法
    # ============================================================
    
    def _log(self, msg):
        """
        添加日志到日志区
        
        参数:
            msg: 日志内容
        """
        ts = time.strftime("%H:%M:%S")
        self.txt_log.insert(tk.END, f"[{ts}] {msg}\n")
        self.txt_log.see(tk.END)  # 自动滚动到底部

    def _set_progress(self, val):
        """设置进度条值（0-100）"""
        self.progress['value'] = val

    def _set_status(self, txt):
        """设置状态栏文本"""
        self.var_status.set(txt)

    def _refresh_ports(self):
        """刷新可用串口列表"""
        # 获取系统所有串口
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.cb_port['values'] = ports
        if ports:
            self.cb_port.set(ports[0])  # 默认选中第一个
        self._log(f"发现 {len(ports)} 个串口")

    # ============================================================
    # 串口连接管理
    # ============================================================
    
    def _toggle_conn(self):
        """切换串口连接状态"""
        if self.is_connected:
            self._do_disconnect()
        else:
            self._do_connect()

    def _do_connect(self):
        """
        连接串口
        
        流程:
            1. 获取用户选择的串口号和波特率
            2. 创建serial.Serial对象
            3. 更新界面状态
        """
        port = self.cb_port.get()
        if not port:
            messagebox.showwarning("提示", "请选择串口")
            return
        
        try:
            baud = int(self.cb_baud.get())
            # 创建串口对象，timeout=0.1秒用于非阻塞读取
            self.serial_port = serial.Serial(port, baud, timeout=0.1)
            self.is_connected = True
            
            # 更新界面：禁用串口选择，启用操作按钮
            self.btn_conn.configure(text="断开")
            self.cb_port.state(['disabled'])
            self.cb_baud.state(['disabled'])
            self.btn_upload.state(['!disabled'])
            self.chk_auto.state(['!disabled'])
            
            self._log(f"已连接 {port} @ {baud}")
            self._set_status(f"已连接: {port}")
            
        except Exception as e:
            messagebox.showerror("错误", f"连接失败: {e}")

    def _do_disconnect(self):
        """
        断开串口连接
        
        流程:
            1. 停止自动检测模式（如果正在运行）
            2. 关闭串口
            3. 恢复界面状态
        """
        # 先停止自动模式
        self._stop_auto_mode()
        
        # 关闭串口
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        
        self.is_connected = False
        
        # 恢复界面状态
        self.btn_conn.configure(text="连接")
        self.cb_port.state(['!disabled'])
        self.cb_baud.state(['!disabled'])
        self.btn_upload.state(['disabled'])
        self.chk_auto.state(['disabled'])
        self.var_auto.set(False)
        
        self._log("已断开")
        self._set_status("已断开")

    # ============================================================
    # 文件选择
    # ============================================================
    
    def _browse(self):
        """
        浏览选择固件文件
        
        功能:
            - 打开文件对话框选择.bin文件
            - 显示文件大小
            - 检查是否超过54KB限制
        """
        path = filedialog.askopenfilename(
            title="选择固件",
            filetypes=[("BIN文件", "*.bin"), ("所有文件", "*.*")]
        )
        if not path:
            return
        
        # 保存文件路径
        self.file_path = path
        self.entry_file.delete(0, tk.END)
        self.entry_file.insert(0, path)
        
        # 获取文件大小并显示
        size = os.path.getsize(path)
        if size > self.MAX_FIRMWARE_SIZE:
            # 超过限制，红色警告
            self.lbl_info.configure(text=f"{size} 字节 (超出54KB限制!)", foreground='red')
        else:
            # 正常显示
            self.lbl_info.configure(text=f"{size} 字节 ({size/1024:.1f} KB)", foreground='black')
        
        self._log(f"选择: {os.path.basename(path)} ({size} B)")

    # ============================================================
    # 自动检测模式
    # ============================================================
    
    def _toggle_auto_mode(self):
        """切换自动检测模式开关"""
        if self.var_auto.get():
            self._start_auto_mode()
        else:
            self._stop_auto_mode()

    def _start_auto_mode(self):
        """
        启动自动检测模式
        
        功能:
            - 启动后台线程监听串口
            - 检测到"倒计时"自动触发升级
            - 升级完成后继续监听
        """
        if not self.file_path:
            messagebox.showwarning("提示", "请先选择固件文件")
            self.var_auto.set(False)
            return
        
        self.auto_mode = True
        self.stop_auto = False
        
        # 自动模式下禁用手动按钮，避免冲突
        self.btn_upload.state(['disabled'])
        
        self._log(">> 自动检测模式已启动，等待设备进入boot模式...")
        self._set_status("自动检测中...")
        
        # 启动后台监听线程
        self.auto_thread = threading.Thread(target=self._auto_detect_loop, daemon=True)
        self.auto_thread.start()

    def _stop_auto_mode(self):
        """停止自动检测模式"""
        self.auto_mode = False
        self.stop_auto = True
        
        # 恢复手动按钮
        if self.is_connected:
            self.btn_upload.state(['!disabled'])
        
        self._log(">> 自动检测模式已停止")
        self._set_status("已停止自动检测")

    def _auto_detect_loop(self):
        """
        自动检测主循环（在后台线程运行）
        
        工作原理:
            1. 持续监听串口数据（50ms轮询）
            2. 检测到"倒计时"关键词，说明设备在boot模式空闲等待
            3. 自动触发升级流程
            4. 升级完成后继续监听（等待下次boot）
            
        关键点:
            - "倒计时"是bootloader空闲时的特征输出
            - 自动模式会重新读取bin文件（支持编译后大小变化）
        """
        # 清空串口缓冲区
        self.serial_port.reset_input_buffer()
        
        while not self.stop_auto and self.is_connected:
            try:
                # 检查是否有数据到达
                if self.serial_port.in_waiting:
                    # 读取所有可用数据
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    text = data.decode('gbk', errors='ignore')  # 设备使用GBK编码
                    
                    # 检测到"倒计时"说明设备处于boot空闲状态
                    if "倒计时" in text:
                        self._log(f"[RX] {text.strip()}")
                        self._log(">> 检测到设备处于boot模式，自动开始升级!")
                        
                        # 更新状态
                        self.root.after(0, lambda: self._set_status("自动升级中..."))
                        
                        # 执行升级
                        ok, msg = self._do_upload()
                        
                        # 处理结果
                        if ok:
                            self._log(f">> 自动升级成功: {msg}")
                            self.root.after(0, lambda: self._set_status("升级完成"))
                            self.root.after(0, lambda m=msg: messagebox.showinfo("成功", m))
                        else:
                            self._log(f">> 自动升级失败: {msg}")
                            self.root.after(0, lambda: self._set_status("升级失败"))
                            self.root.after(0, lambda m=msg: messagebox.showerror("失败", m))
                        
                        # 升级完成后继续监听
                        self.serial_port.reset_input_buffer()
                        self._log(">> 继续监听...")
                        self.root.after(0, lambda: self._set_status("自动检测中..."))
                
                # 50ms轮询间隔
                time.sleep(0.05)
                
            except Exception as e:
                if not self.stop_auto:
                    self._log(f"自动检测异常: {e}")
                break
        
        self.root.after(0, lambda: self._set_status("自动检测已停止"))

    # ============================================================
    # 串口通信辅助
    # ============================================================
    
    def _wait_for_signal(self, signal_text, timeout=5.0):
        """
        实时读取串口，等待指定信号出现
        
        参数:
            signal_text: 要等待的关键词（如"确认"、"进入接收数据状态"）
            timeout: 超时时间（秒）
            
        返回:
            (found, response): found表示是否找到信号，response是累积的响应文本
            
        工作原理:
            - 持续读取串口数据
            - 每次读取后检查是否包含目标信号
            - 找到信号或超时后返回
        """
        resp = b''
        end_time = time.time() + timeout
        
        while time.time() < end_time:
            # 读取当前缓冲区中的所有数据
            chunk = self.serial_port.read(self.serial_port.in_waiting)
            if chunk:
                resp += chunk
                # 解码并检查是否包含目标信号
                text = resp.decode('gbk', errors='ignore')
                if signal_text in text:
                    return True, text.strip()
                time.sleep(0.01)
            else:
                time.sleep(0.01)
        
        # 超时，返回已收到的内容
        return False, resp.decode('gbk', errors='ignore').strip()

    def _read_response(self, timeout=1.0):
        """
        读取串口响应（等待固定时间后返回）
        
        参数:
            timeout: 等待时间（秒）
            
        返回:
            解码后的响应文本
        """
        resp = b''
        end_time = time.time() + timeout
        
        while time.time() < end_time:
            chunk = self.serial_port.read(self.serial_port.in_waiting)
            if chunk:
                resp += chunk
                time.sleep(0.02)
            else:
                time.sleep(0.02)
        
        return resp.decode('gbk', errors='ignore').strip()

    # ============================================================
    # 升级流程
    # ============================================================
    
    def _start_upload(self):
        """
        手动触发升级（点击"一键下载"按钮）
        
        流程:
            1. 检查是否正在升级
            2. 验证固件文件
            3. 启动升级线程
        """
        if self.is_uploading:
            return
        
        if not self.file_path:
            messagebox.showwarning("提示", "请先选择固件文件")
            return
        
        # 验证文件大小
        size = os.path.getsize(self.file_path)
        if size == 0:
            messagebox.showerror("错误", "固件文件为空")
            return
        if size > self.MAX_FIRMWARE_SIZE:
            messagebox.showerror("错误", f"文件超过 {self.MAX_FIRMWARE_SIZE//1024}KB 限制")
            return

        # 标记正在升级，禁用按钮
        self.is_uploading = True
        self.btn_upload.state(['disabled'])
        self.btn_browse.state(['disabled'])
        
        # 在新线程中执行升级（避免阻塞UI）
        threading.Thread(target=self._upload_thread, daemon=True).start()

    def _upload_thread(self):
        """升级线程包装（处理异常）"""
        try:
            ok, msg = self._do_upload()
        except Exception as e:
            ok, msg = False, str(e)
        # 升级完成后在主线程更新UI
        self.root.after(0, lambda: self._upload_done(ok, msg))

    def _do_upload(self):
        """
        执行升级的核心函数
        
        协议流程:
            1. 读取固件文件（每次重新读取，支持编译后大小变化）
            2. 发送握手命令: upgrade:"<文件大小>"
            3. 等待设备确认（检查"确认"关键词）
            4. 等待Flash擦除完成（检查"进入接收数据状态"）
            5. 分包发送固件数据（256字节/包，20ms间隔）
            6. 等待设备确认写入完成（检查"写入完成"和"长度正确"）
            
        返回:
            (success, message): success表示是否成功，message是结果描述
        """
        ser = self.serial_port

        # ① 重新读取固件文件（支持编译后大小变化）
        try:
            with open(self.file_path, 'rb') as f:
                fw = f.read()
        except Exception as e:
            return False, f"读取文件失败: {e}"
        
        fw_len = len(fw)

        # 更新界面显示的文件大小
        self.root.after(0, lambda: self.lbl_info.configure(
            text=f"{fw_len} 字节 ({fw_len/1024:.1f} KB)", foreground='black'))

        self._log("=" * 45)
        self._log(f"开始升级  固件: {fw_len} 字节")
        self._set_status("正在升级...")

        # ② 清空串口缓冲区
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.1)

        # ③ 发送握手包头: upgrade:"<文件大小>"
        #    格式固定，设备会解析这个命令获取文件大小
        cmd = f'upgrade:"{fw_len}"'
        self._log(f"[TX] {cmd}")
        ser.write(cmd.encode('ascii'))
        ser.flush()

        # ④ 等待设备确认握手
        #    设备会回复: "已确认即将接受的数据包长度为: XXXX"
        self._log("等待设备确认...")
        found, resp = self._wait_for_signal("确认", timeout=3.0)

        if not resp:
            return False, "设备无响应，请确认设备处于boot模式"

        self._log(f"[RX] {resp}")

        if not found:
            return False, f"握手失败，设备响应: {resp}"

        # ⑤ 等待Flash擦除完成
        #    设备擦除Flash后返回: "Flash擦除完成，进入接收数据状态..."
        #    关键: 收到这个信号后必须立即开始发数据，否则会超时
        self._log("等待Flash擦除完成...")
        found, resp = self._wait_for_signal("进入接收数据状态", timeout=10.0)

        # 打印擦除过程日志
        for line in resp.split('\n'):
            line = line.strip()
            if line:
                self._log(f"[RX] {line}")

        if not found:
            self._log("未收到明确就绪信号，尝试发送...")

        # ⑥ 收到"进入接收数据状态"后立即开始发数据
        #    设备此时开始5秒倒计时，必须在超时前发送数据
        self._log("开始发送数据...")
        sent = 0
        while sent < fw_len:
            # 计算本次发送的数据块
            end = min(sent + self.CHUNK_SIZE, fw_len)
            chunk = fw[sent:end]
            
            # 发送数据
            ser.write(chunk)
            ser.flush()
            sent = end

            # 更新进度条和状态（在主线程中执行）
            pct = sent / fw_len * 100
            self.root.after(0, lambda v=pct: self._set_progress(v))
            self.root.after(0, lambda s=sent: self._set_status(
                f"传输中 {s}/{fw_len} ({s*100/fw_len:.1f}%)"))

            # 分包间隔，让设备有时间处理
            time.sleep(self.CHUNK_DELAY)

        self._log(f"数据发送完毕: {sent} 字节")

        # ⑦ 等待设备确认写入完成
        #    设备超时3秒后判断传输完成
        #    返回: "数据写入完成，长度正确: XXXX"
        self._log("等待设备确认...")
        resp = self._read_response(timeout=5.0)

        if not resp:
            return False, "未收到设备确认，传输可能失败"

        self._log(f"[RX] {resp}")

        # ⑧ 检查是否写入成功
        if "写入完成" in resp and "长度正确" in resp:
            self._log("升级成功!")
            self._set_status("升级完成")
            return True, "固件升级成功!"
        elif "跳转" in resp:
            # 设备可能已经跳转到APP，也算成功
            self._log("设备已跳转，升级成功!")
            self._set_status("升级完成")
            return True, "固件升级成功!"
        else:
            return False, f"升级失败，设备响应: {resp}"

    def _upload_done(self, ok, msg):
        """
        升级完成回调（在主线程执行）
        
        参数:
            ok: 是否成功
            msg: 结果消息
        """
        # 恢复按钮状态
        self.is_uploading = False
        self.btn_upload.state(['!disabled'])
        self.btn_browse.state(['!disabled'])
        self.progress['value'] = 0
        
        # 显示结果对话框
        if ok:
            messagebox.showinfo("成功", msg)
        else:
            messagebox.showerror("失败", msg)

    def run(self):
        """启动主循环"""
        self.root.mainloop()


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    app = BootloaderGUI()
    app.run()
