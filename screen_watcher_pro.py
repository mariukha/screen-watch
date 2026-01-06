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
                return {"token": c.get("token", DEFAULT_TOKEN), "chat": c.get("chat", DEFAULT_CHAT),
                        "threshold": c.get("threshold", 50000), "interval": c.get("interval", 1.0), "region": c.get("region")}
        except: return {"token": DEFAULT_TOKEN, "chat": DEFAULT_CHAT, "threshold": 50000, "interval": 1.0, "region": None}
    
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

class Monitor(QThread):
    log = pyqtSignal(str, str)
    changed = pyqtSignal(object, int)
    status = pyqtSignal(str)
    
    def __init__(self, token, chat, region, threshold, interval):
        super().__init__()
        self.token, self.chat, self.region = token, chat, tuple(region) if region else None
        self.threshold, self.interval, self.running, self.last, self.count = threshold, interval, True, None, 0
        
    def run(self):
        self.log.emit(f"Started | Region: {self.region} | Threshold: {self.threshold:,}", "ok")
        try: self.last = screenshot(self.region)
        except Exception as e: self.log.emit(f"Capture error: {e}", "err"); return
        n = 0
        while self.running:
            time.sleep(self.interval)
            if not self.running: break
            try:
                cur = screenshot(self.region)
                n += 1
                diff = np.array(ImageChops.difference(self.last, cur), dtype=np.int64)
                score = int(np.sum(diff))
                if n % 5 == 0: self.status.emit(f"Active | #{n} | diff: {score:,}")
                if score > self.threshold:
                    self.count += 1
                    self.log.emit(f"CHANGE #{self.count} | Score: {score:,}", "warn")
                    if self.send(cur, score): self.log.emit("Sent to Telegram", "ok")
                    else: self.log.emit("Failed to send", "err")
                    self.last = cur.copy()
                    self.changed.emit(cur, score)
            except Exception as e: self.log.emit(f"Error: {e}", "err"); time.sleep(2)
    
    def send(self, img, score):
        try:
            bio = BytesIO()
            img.save(bio, 'PNG')
            bio.seek(0)
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendPhoto",
                data={'chat_id': self.chat, 'caption': f"⚡ Change | Score: {score:,} | {datetime.now().strftime('%H:%M:%S')}"},
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
        self.setStyleSheet("background:#1a1a1a;border:1px solid #333;border-radius:8px;")
    def set(self, img): self.pm = to_pixmap(img); self.update()
    def clear(self): self.pm = None; self.update()
    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.pm:
            s = self.pm.scaled(self.size() - QSize(16,16), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((self.width()-s.width())//2, (self.height()-s.height())//2, s)
        else: p.setPen(QColor("#555")); p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No preview")

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(500, 780)
        self.resize(500, 820)
        self.cfg = Config.load()
        self.mon = None
        self.sel = None
        self._ui()
        self._tray()
        self._region()
    
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

        layout.addWidget(QLabel("Monitor Region", objectName="header_reg"))
        self.findChild(QLabel, "header_reg").setProperty("class", "header")

        self.pv = Preview()
        pv_layout = QHBoxLayout()
        pv_layout.addStretch()
        pv_layout.addWidget(self.pv)
        pv_layout.addStretch()
        layout.addLayout(pv_layout)

        self.rl = QLabel("No region selected")
        self.rl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.rl)

        self.selb = QPushButton("Select Region")
        self.selb.clicked.connect(self._sel)
        layout.addWidget(self.selb)

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
    
    def _region(self):
        r = self.cfg.get("region")
        if r:
            self.rl.setText(f"({r[0]}, {r[1]}) • {r[2]}×{r[3]}")
            self.rl.setStyleSheet("color:#4ec9b0;padding:8px;")
            try: self.pv.set(screenshot(tuple(r)))
            except: pass
        else:
            self.rl.setText("No region selected")
            self.rl.setStyleSheet("color:#888888;padding:8px;")
            self.pv.clear()
    
    def _sel(self):
        self.sel = Selector()
        self.sel.selected.connect(self._on_sel)
        self.sel.cancelled.connect(self._on_cancel)
        self.sel.show()
        self.hide()
        QTimer.singleShot(250, self.sel.take_screenshot)
    
    def _on_sel(self, r):
        self.cfg["region"] = list(r)
        self._region()
        self._log(f"Region: {r}", "ok")
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
        r = self.cfg.get("region")
        if not r: QMessageBox.warning(self, "Error", "Select region first!"); return
        tok, chat = self.tok.text().strip(), self.chat.text().strip()
        if not tok or not chat: QMessageBox.warning(self, "Error", "Enter Token and Chat ID!"); return
        self.cfg["token"], self.cfg["chat"] = tok, chat
        self.cfg["threshold"], self.cfg["interval"] = self.thr.value(), self.intv.value()
        Config.save(self.cfg)
        self.mon = Monitor(tok, chat, r, self.cfg["threshold"], self.cfg["interval"])
        self.mon.log.connect(self._log)
        self.mon.status.connect(lambda s: self.st.setText(s))
        self.mon.changed.connect(lambda img, _: self.pv.set(img))
        self.mon.start()
        self.startb.setEnabled(False); self.stopb.setEnabled(True); self.selb.setEnabled(False)
        self.tok.setEnabled(False); self.chat.setEnabled(False); self.testb.setEnabled(False)
        self.dot.setStyleSheet("color:#4ec9b0;font-size:12px;")
        self.st.setText("Active"); self.st.setStyleSheet("color:#4ec9b0;font-size:12px;")
    
    def _stop(self):
        if self.mon: self.mon.stop(); self.mon = None
        self._log("Stopped", "info")
        self.startb.setEnabled(True); self.stopb.setEnabled(False); self.selb.setEnabled(True)
        self.tok.setEnabled(True); self.chat.setEnabled(True); self.testb.setEnabled(True)
        self.dot.setStyleSheet("color:#888888;font-size:12px;")
        self.st.setText("Idle"); self.st.setStyleSheet("color:#cccccc;font-size:12px;")
    
    def closeEvent(self, e): self._stop(); Config.save(self.cfg); e.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    w = Main()
    w.show()
    w.raise_()
    w.activateWindow()
    sys.exit(app.exec())
