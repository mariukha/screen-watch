import sys
import json
import time
import requests
import numpy as np
from PIL import Image, ImageChops
from datetime import datetime
from io import BytesIO
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QLineEdit, QPushButton, QTextEdit, QSpinBox, QDoubleSpinBox, 
    QGroupBox, QSystemTrayIcon, QMenu, QMessageBox, QFrame, QStyle
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QRect, QPoint, QSize
from PyQt6.QtGui import QPainter, QColor, QPen, QPixmap, QAction, QImage, QFont
import pyautogui

APP_NAME = "ScreenWatch"
CONFIG_FILE = "config.json"
DEFAULT_TOKEN = ""
DEFAULT_CHAT = ""

class Config:
    @staticmethod
    def load():
        try:
            with open(CONFIG_FILE, 'r') as f:
                c = json.load(f)
                # Migrate old single region to regions list
                regions = c.get("regions", [])
                if not regions and c.get("region"):
                    regions = [c.get("region")]
                return {"token": c.get("token", DEFAULT_TOKEN), "chat": c.get("chat", DEFAULT_CHAT),
                        "threshold": c.get("threshold", 50000), "interval": c.get("interval", 1.0), "regions": regions}
        except: return {"token": DEFAULT_TOKEN, "chat": DEFAULT_CHAT, "threshold": 50000, "interval": 1.0, "regions": []}
    
    @staticmethod
    def save(d):
        with open(CONFIG_FILE, 'w') as f: json.dump(d, f, indent=2)

def screenshot(region=None):
    return pyautogui.screenshot(region=region).convert("RGB")

def to_pixmap(img):
    if img.mode == "RGB":
        r, g, b = img.split()
        img = Image.merge("RGB", (b, g, r))
    data = img.convert("RGBA").tobytes("raw", "BGRA")
    return QPixmap.fromImage(QImage(data, img.size[0], img.size[1], QImage.Format.Format_ARGB32))

class TelegramListener(QThread):
    log = pyqtSignal(str, str)
    screenshot_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    start_requested = pyqtSignal()
    
    def __init__(self, token, chat, idle_mode=False):
        super().__init__()
        self.token, self.chat = token, chat
        self.running = True
        self.offset = 0
        self.idle_mode = idle_mode  # True = only listen for !start
    
    def run(self):
        mode = "idle" if self.idle_mode else "active"
        self.log.emit(f"Telegram listener started ({mode})", "ok")
        while self.running:
            try:
                r = requests.get(
                    f"https://api.telegram.org/bot{self.token}/getUpdates",
                    params={'offset': self.offset, 'timeout': 10},
                    timeout=15
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get('ok'):
                        for update in data.get('result', []):
                            self.offset = update['update_id'] + 1
                            msg = update.get('message', {})
                            text = msg.get('text', '').strip().lower()
                            chat_id = str(msg.get('chat', {}).get('id', ''))
                            if chat_id == str(self.chat):
                                if self.idle_mode:
                                    # Only respond to !start when idle
                                    if text == '!start':
                                        self.log.emit("Start requested via Telegram", "warn")
                                        self._send_message("▶️ Monitoring started")
                                        self.start_requested.emit()
                                else:
                                    # Active mode - respond to !screen and !stop
                                    if text == '!screen':
                                        self.log.emit("Screenshot requested via Telegram", "warn")
                                        self.screenshot_requested.emit()
                                    elif text == '!stop':
                                        self.log.emit("Stop requested via Telegram", "warn")
                                        self._send_message("🛑 Monitoring stopped")
                                        self.stop_requested.emit()
            except Exception as e:
                if self.running:
                    self.log.emit(f"Listener error: {e}", "err")
                    time.sleep(5)
    
    def _send_message(self, text):
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data={'chat_id': self.chat, 'text': text},
                timeout=10
            )
        except: pass
    
    def stop(self):
        self.running = False
        self.wait(3000)


class Monitor(QThread):
    log = pyqtSignal(str, str)
    changed = pyqtSignal(object, int, int)  # img, score, zone_index
    status = pyqtSignal(str)
    
    def __init__(self, token, chat, regions, threshold, interval):
        super().__init__()
        self.token, self.chat = token, chat
        self.regions = [tuple(r) for r in regions] if regions else []
        self.threshold, self.interval, self.running = threshold, interval, True
        self.last_images = {}  # Store last image per region index
        self.count = 0
        
    def run(self):
        self.log.emit(f"Started | Zones: {len(self.regions)} | Threshold: {self.threshold:,}", "ok")
        # Initialize last images for all regions
        for i, region in enumerate(self.regions):
            try:
                self.last_images[i] = screenshot(region)
                self.log.emit(f"Zone {i+1}: {region}", "info")
            except Exception as e:
                self.log.emit(f"Zone {i+1} capture error: {e}", "err")
                return
        n = 0
        while self.running:
            time.sleep(self.interval)
            if not self.running: break
            try:
                for i, region in enumerate(self.regions):
                    cur = screenshot(region)
                    n += 1
                    diff = np.array(ImageChops.difference(self.last_images[i], cur), dtype=np.int64)
                    score = int(np.sum(diff))
                    if n % (5 * len(self.regions)) == 0:
                        self.status.emit(f"Active | #{n} | zones: {len(self.regions)}")
                    if score > self.threshold:
                        self.count += 1
                        self.log.emit(f"CHANGE Zone {i+1} #{self.count} | Score: {score:,}", "warn")
                        if self.send(cur, score, i+1):
                            self.log.emit(f"Zone {i+1} sent to Telegram", "ok")
                        else:
                            self.log.emit(f"Zone {i+1} failed to send", "err")
                        self.last_images[i] = cur.copy()
                        self.changed.emit(cur, score, i)
            except Exception as e:
                self.log.emit(f"Error: {e}", "err")
                time.sleep(2)
    
    def send(self, img, score, zone_num):
        try:
            bio = BytesIO()
            img.save(bio, 'PNG')
            bio.seek(0)
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendPhoto",
                data={'chat_id': self.chat, 'caption': f"⚡ Zone {zone_num} Change | Score: {score:,} | {datetime.now().strftime('%H:%M:%S')}"},
                files={'photo': ('s.png', bio, 'image/png')}, timeout=30)
            return r.status_code == 200
        except: return False
    
    def stop(self): self.running = False; self.wait(3000)

class Selector(QWidget):
    selected = pyqtSignal(tuple)
    cancelled = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        geo = QRect()
        for s in QApplication.screens(): geo = geo.united(s.geometry())
        self.setGeometry(geo)
        self.bg = None
        self.is_ready = False
        self.p1, self.p2, self.sel = QPoint(), QPoint(), False
        self.setCursor(Qt.CursorShape.CrossCursor)
    
    def take_screenshot(self):
        try: self.bg = to_pixmap(pyautogui.screenshot())
        except: self.bg = None
        self.is_ready = True
        self.update()
    
    def paintEvent(self, e):
        if not self.is_ready: return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.bg: p.drawPixmap(self.rect(), self.bg)
        p.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if self.sel:
            r = QRect(self.p1, self.p2).normalized()
            if self.bg: p.setClipRect(r); p.drawPixmap(self.rect(), self.bg); p.setClipping(False)
            p.setPen(QPen(QColor("#00ff88"), 2)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawRect(r)
            p.setFont(QFont("Menlo", 11, QFont.Weight.Bold)); p.setPen(QColor(255,255,255))
            p.drawText(r.adjusted(5,5,0,0), Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignLeft, f"{r.width()} × {r.height()}")
        p.setFont(QFont("Menlo", 13)); p.setPen(QColor(255,255,255,180))
        p.drawText(self.rect().adjusted(0,0,0,-20), Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignBottom, "Draw selection • ESC cancel")
    
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self.p1 = self.p2 = e.pos(); self.sel = True; self.update()
    def mouseMoveEvent(self, e):
        if self.sel: self.p2 = e.pos(); self.update()
    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.sel:
            self.sel = False; r = QRect(self.p1, self.p2).normalized()
            if r.width() > 10 and r.height() > 10: self.selected.emit(tuple(r.getRect()))
            else: self.cancelled.emit()
            self.close()
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape: self.cancelled.emit(); self.close()

class Preview(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedSize(300, 180)
        self.pm = None
        self.zone_label = ""
        self.setStyleSheet("background:#1a1a1a;border:1px solid #333;border-radius:8px;")
    def set(self, img, label=""): self.pm = to_pixmap(img); self.zone_label = label; self.update()
    def clear(self): self.pm = None; self.zone_label = ""; self.update()
    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.pm:
            s = self.pm.scaled(self.size() - QSize(16,16), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((self.width()-s.width())//2, (self.height()-s.height())//2, s)
            if self.zone_label:
                p.setPen(QColor("#4ec9b0"))
                p.setFont(QFont("Menlo", 10, QFont.Weight.Bold))
                p.drawText(8, 18, self.zone_label)
        else: p.setPen(QColor("#555")); p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No preview")

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(500, 850)
        self.resize(500, 900)
        self.cfg = Config.load()
        self.mon = None
        self.sel = None
        self.listener = None
        self.idle_listener = None
        self.current_zone_idx = 0  # For cycling through zones in preview
        self._ui()
        self._tray()
        self._update_regions_display()
        self._start_idle_listener()
    
    def _ui(self):
        w = QWidget()
        self.setCentralWidget(w)
        
        self.setStyleSheet("""
            QWidget { background-color: #252526; color: #cccccc; font-family: -apple-system, sans-serif; font-size: 13px; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit {
                background-color: #333333; border: 1px solid #3c3c3c; border-radius: 4px; padding: 6px; color: #ffffff; 
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #007acc; }
            QPushButton {
                background-color: #3c3c3c; border: 1px solid #505050; border-radius: 4px; padding: 8px 16px; color: #ffffff; 
            }
            QPushButton:hover { background-color: #4a4a4a; }
            QPushButton:pressed { background-color: #2a2a2a; }
            QPushButton:disabled { background-color: #2d2d2d; color: #666666; border-color: #333333; }
            QLabel.header { font-weight: bold; font-size: 14px; color: #ffffff; margin-top: 15px; margin-bottom: 5px; }
            QLabel.status_active { color: #4ec9b0; }
            QLabel.status_idle { color: #cccccc; }
        """)

        layout = QVBoxLayout(w)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        h_layout = QHBoxLayout()
        h_layout.addStretch()
        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff;")
        h_layout.addWidget(title)
        h_layout.addStretch()
        self.dot = QLabel("●")
        self.st = QLabel("Idle")
        h_layout.addWidget(self.dot)
        h_layout.addWidget(self.st)
        layout.addLayout(h_layout)

        layout.addWidget(QLabel("Telegram", objectName="header_tg"))
        self.findChild(QLabel, "header_tg").setProperty("class", "header")

        self.tok = QLineEdit()
        self.tok.setPlaceholderText("Bot Token")
        self.tok.setText(self.cfg.get("token", ""))
        self.tok.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.tok)

        chat_layout = QHBoxLayout()
        self.chat = QLineEdit()
        self.chat.setPlaceholderText("Chat ID")
        self.chat.setText(str(self.cfg.get("chat", "")))
        chat_layout.addWidget(self.chat)
        
        self.testb = QPushButton("Test")
        self.testb.setFixedWidth(80)
        self.testb.clicked.connect(self._test)
        chat_layout.addWidget(self.testb)
        layout.addLayout(chat_layout)

        layout.addWidget(QLabel("Monitor Regions", objectName="header_reg"))
        self.findChild(QLabel, "header_reg").setProperty("class", "header")

        self.pv = Preview()
        pv_layout = QHBoxLayout()
        pv_layout.addStretch()
        pv_layout.addWidget(self.pv)
        pv_layout.addStretch()
        layout.addLayout(pv_layout)

        self.rl = QLabel("No regions selected")
        self.rl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.rl)

        # Zone navigation buttons
        zone_nav_layout = QHBoxLayout()
        self.prev_zone_btn = QPushButton("◀")
        self.prev_zone_btn.setFixedWidth(40)
        self.prev_zone_btn.clicked.connect(self._prev_zone)
        self.zone_indicator = QLabel("Zone 0/0")
        self.zone_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_zone_btn = QPushButton("▶")
        self.next_zone_btn.setFixedWidth(40)
        self.next_zone_btn.clicked.connect(self._next_zone)
        zone_nav_layout.addWidget(self.prev_zone_btn)
        zone_nav_layout.addStretch()
        zone_nav_layout.addWidget(self.zone_indicator)
        zone_nav_layout.addStretch()
        zone_nav_layout.addWidget(self.next_zone_btn)
        layout.addLayout(zone_nav_layout)

        # Region buttons
        reg_btn_layout = QHBoxLayout()
        self.selb = QPushButton("+ Add Zone")
        self.selb.clicked.connect(self._sel)
        self.clearb = QPushButton("Clear All")
        self.clearb.clicked.connect(self._clear_all_zones)
        self.clearb.setStyleSheet("background-color: #6e3630;")
        self.remove_zone_btn = QPushButton("Remove Zone")
        self.remove_zone_btn.clicked.connect(self._remove_current_zone)
        self.remove_zone_btn.setStyleSheet("background-color: #4a3030;")
        reg_btn_layout.addWidget(self.selb)
        reg_btn_layout.addWidget(self.remove_zone_btn)
        reg_btn_layout.addWidget(self.clearb)
        layout.addLayout(reg_btn_layout)

        layout.addWidget(QLabel("Settings", objectName="header_set"))
        self.findChild(QLabel, "header_set").setProperty("class", "header")

        sett_layout = QHBoxLayout()
        
        v1 = QVBoxLayout(); v1.setSpacing(2)
        v1.addWidget(QLabel("Threshold"))
        self.thr = QSpinBox()
        self.thr.setRange(1000, 100000000)
        self.thr.setSingleStep(10000)
        self.thr.setValue(int(self.cfg.get("threshold", 50000)))
        v1.addWidget(self.thr)
        sett_layout.addLayout(v1)

        v2 = QVBoxLayout(); v2.setSpacing(2)
        v2.addWidget(QLabel("Interval (sec)"))
        self.intv = QDoubleSpinBox()
        self.intv.setRange(0.3, 30.0)
        self.intv.setSingleStep(0.5)
        self.intv.setValue(float(self.cfg.get("interval", 1.0)))
        v2.addWidget(self.intv)
        sett_layout.addLayout(v2)
        
        layout.addLayout(sett_layout)

        layout.addSpacing(10)

        act_layout = QHBoxLayout()
        self.startb = QPushButton("Start Monitoring")
        self.startb.setFixedHeight(40)
        self.startb.setStyleSheet("background-color: #2ea043; border: none; font-weight: bold;")
        self.startb.clicked.connect(self._start)
        
        self.stopb = QPushButton("Stop")
        self.stopb.setFixedHeight(40)
        self.stopb.setStyleSheet("background-color: #d73a49; border: none; font-weight: bold;")
        self.stopb.clicked.connect(self._stop)
        self.stopb.setEnabled(False)
        
        act_layout.addWidget(self.startb)
        act_layout.addWidget(self.stopb)
        layout.addLayout(act_layout)

        self.logw = QTextEdit()
        self.logw.setReadOnly(True)
        self.logw.setFixedHeight(100)
        layout.addWidget(self.logw)
    
    def _tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        m = QMenu()
        a1 = QAction("Show", self); a1.triggered.connect(self.show); m.addAction(a1)
        a2 = QAction("Quit", self); a2.triggered.connect(QApplication.quit); m.addAction(a2)
        self.tray.setContextMenu(m)
        self.tray.show()
    
    def _log(self, msg, lv="info"):
        c = {"info":"#cccccc","ok":"#4ec9b0","warn":"#dcdcaa","err":"#f44747"}.get(lv,"#cccccc")
        self.logw.append(f'<span style="color:#888888">[{datetime.now().strftime("%H:%M:%S")}]</span> <span style="color:{c}">{msg}</span>')
        self.logw.verticalScrollBar().setValue(self.logw.verticalScrollBar().maximum())
    
    def _update_regions_display(self):
        regions = self.cfg.get("regions", [])
        count = len(regions)
        if count > 0:
            self.rl.setText(f"{count} zone(s) selected")
            self.rl.setStyleSheet("color:#4ec9b0;padding:8px;")
            if self.current_zone_idx >= count:
                self.current_zone_idx = count - 1
            self._show_current_zone()
        else:
            self.rl.setText("No regions selected")
            self.rl.setStyleSheet("color:#888888;padding:8px;")
            self.pv.clear()
            self.zone_indicator.setText("Zone 0/0")
            self.current_zone_idx = 0
    
    def _show_current_zone(self):
        regions = self.cfg.get("regions", [])
        if regions and 0 <= self.current_zone_idx < len(regions):
            r = regions[self.current_zone_idx]
            self.zone_indicator.setText(f"Zone {self.current_zone_idx + 1}/{len(regions)}")
            try:
                self.pv.set(screenshot(tuple(r)), f"Zone {self.current_zone_idx + 1}: {r[2]}×{r[3]}")
            except: pass
        else:
            self.zone_indicator.setText("Zone 0/0")
            self.pv.clear()
    
    def _prev_zone(self):
        regions = self.cfg.get("regions", [])
        if regions:
            self.current_zone_idx = (self.current_zone_idx - 1) % len(regions)
            self._show_current_zone()
    
    def _next_zone(self):
        regions = self.cfg.get("regions", [])
        if regions:
            self.current_zone_idx = (self.current_zone_idx + 1) % len(regions)
            self._show_current_zone()
    
    def _remove_current_zone(self):
        regions = self.cfg.get("regions", [])
        if regions and 0 <= self.current_zone_idx < len(regions):
            removed = regions.pop(self.current_zone_idx)
            self.cfg["regions"] = regions
            self._log(f"Removed zone {self.current_zone_idx + 1}", "info")
            self._update_regions_display()
    
    def _clear_all_zones(self):
        self.cfg["regions"] = []
        self.current_zone_idx = 0
        self._log("All zones cleared", "info")
        self._update_regions_display()
    
    def _sel(self):
        self.sel = Selector()
        self.sel.selected.connect(self._on_sel)
        self.sel.cancelled.connect(self._on_cancel)
        self.sel.show()
        self.hide()
        QTimer.singleShot(250, self.sel.take_screenshot)
    
    def _on_sel(self, r):
        if "regions" not in self.cfg:
            self.cfg["regions"] = []
        self.cfg["regions"].append(list(r))
        self.current_zone_idx = len(self.cfg["regions"]) - 1
        self._update_regions_display()
        self._log(f"Added zone {len(self.cfg['regions'])}: {r}", "ok")
        self.show(); self.activateWindow()
    
    def _on_cancel(self):
        self._log("Cancelled", "info")
        self.show(); self.activateWindow()
    
    def _test(self):
        tok, chat = self.tok.text().strip(), self.chat.text().strip()
        if not tok or not chat: QMessageBox.warning(self, "Error", "Enter Token and Chat ID"); return
        self._log("Sending test...", "info")
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                data={'chat_id': chat, 'text': f"ScreenWatch connected!\n{datetime.now().strftime('%H:%M:%S')}"},
                timeout=10)
            if r.status_code == 200: self._log("Test sent!", "ok"); QMessageBox.information(self, "OK", "Test sent!")
            else: self._log(f"Failed: {r.text}", "err"); QMessageBox.critical(self, "Error", r.text)
        except Exception as e: self._log(f"Error: {e}", "err"); QMessageBox.critical(self, "Error", str(e))
    
    def _start(self):
        regions = self.cfg.get("regions", [])
        if not regions:
            self._log("Cannot start: no regions selected", "err")
            return
        tok, chat = self.tok.text().strip() or self.cfg.get("token", ""), self.chat.text().strip() or self.cfg.get("chat", "")
        if not tok or not chat:
            self._log("Cannot start: no Token or Chat ID", "err")
            return
        
        # Stop idle listener first
        self._stop_idle_listener()
        
        self.cfg["token"], self.cfg["chat"] = tok, chat
        self.cfg["threshold"], self.cfg["interval"] = self.thr.value(), self.intv.value()
        Config.save(self.cfg)
        self.mon = Monitor(tok, chat, regions, self.cfg["threshold"], self.cfg["interval"])
        self.mon.log.connect(self._log)
        self.mon.status.connect(lambda s: self.st.setText(s))
        self.mon.changed.connect(lambda img, _, idx: self.pv.set(img, f"Zone {idx+1}"))
        self.mon.start()
        
        # Start Telegram listener (active mode)
        self.listener = TelegramListener(tok, chat, idle_mode=False)
        self.listener.log.connect(self._log)
        self.listener.screenshot_requested.connect(self._send_screenshot)
        self.listener.stop_requested.connect(self._stop)
        self.listener.start()
        
        self.startb.setEnabled(False); self.stopb.setEnabled(True); self.selb.setEnabled(False)
        self.clearb.setEnabled(False); self.remove_zone_btn.setEnabled(False)
        self.tok.setEnabled(False); self.chat.setEnabled(False); self.testb.setEnabled(False)
        self.dot.setStyleSheet("color:#4ec9b0;font-size:12px;")
        self.st.setText("Active"); self.st.setStyleSheet("color:#4ec9b0;font-size:12px;")
    
    def _send_screenshot(self):
        """Send screenshots of all zones when requested via Telegram command"""
        regions = self.cfg.get("regions", [])
        tok, chat = self.cfg.get("token"), self.cfg.get("chat")
        
        if not regions:
            self._log("No regions to capture", "warn")
            return
        
        sent_count = 0
        for i, r in enumerate(regions):
            try:
                img = screenshot(tuple(r))
                if i == 0:
                    self.pv.set(img, f"Zone {i+1}")
                bio = BytesIO()
                img.save(bio, 'PNG')
                bio.seek(0)
                res = requests.post(
                    f"https://api.telegram.org/bot{tok}/sendPhoto",
                    data={'chat_id': chat, 'caption': f"📸 Zone {i+1}/{len(regions)} | {datetime.now().strftime('%H:%M:%S')}"},
                    files={'photo': ('screen.png', bio, 'image/png')},
                    timeout=30
                )
                if res.status_code == 200:
                    sent_count += 1
                else:
                    self._log(f"Zone {i+1} failed: {res.text}", "err")
            except Exception as e:
                self._log(f"Zone {i+1} error: {e}", "err")
        
        self._log(f"Screenshots sent: {sent_count}/{len(regions)}", "ok" if sent_count == len(regions) else "warn")
    
    def _start_idle_listener(self):
        """Start listener for !start command when monitoring is stopped"""
        tok, chat = self.cfg.get("token", ""), self.cfg.get("chat", "")
        if tok and chat:
            self.idle_listener = TelegramListener(tok, chat, idle_mode=True)
            self.idle_listener.log.connect(self._log)
            self.idle_listener.start_requested.connect(self._start)
            self.idle_listener.start()
    
    def _stop_idle_listener(self):
        if self.idle_listener:
            self.idle_listener.stop()
            self.idle_listener = None
    
    def _stop(self):
        if self.mon: self.mon.stop(); self.mon = None
        if self.listener: self.listener.stop(); self.listener = None
        self._log("Stopped", "info")
        self.startb.setEnabled(True); self.stopb.setEnabled(False); self.selb.setEnabled(True)
        self.clearb.setEnabled(True); self.remove_zone_btn.setEnabled(True)
        self.tok.setEnabled(True); self.chat.setEnabled(True); self.testb.setEnabled(True)
        self.dot.setStyleSheet("color:#888888;font-size:12px;")
        self.st.setText("Idle"); self.st.setStyleSheet("color:#cccccc;font-size:12px;")
        # Restart idle listener to listen for !start
        self._start_idle_listener()
    
    def closeEvent(self, e):
        self._stop_idle_listener()
        self._stop()
        Config.save(self.cfg)
        e.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    w = Main()
    w.show()
    w.raise_()
    w.activateWindow()
    sys.exit(app.exec())
