import sys
from datetime import datetime

import requests
from PySide6.QtCore import QDate, QTime, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDateEdit, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QTabWidget, QTimeEdit, QVBoxLayout, QWidget, QInputDialog,
)

API = "http://127.0.0.1:8765"


class GUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XZ94")
        self.resize(900, 650)

        self.user_id = QLineEdit()
        self.guild_id = QLineEdit()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.sleep_tab(), "🌙 Sleep")
        self.tabs.addTab(self.schedule_tab(), "🌙 Horarios")

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel("XZ94"))
        form = QFormLayout()
        form.addRow("User ID", self.user_id)
        form.addRow("Server ID", self.guild_id)
        layout.addLayout(form)
        layout.addWidget(self.tabs)
        self.setCentralWidget(root)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def request(self, method, endpoint, **kwargs):
        return requests.request(method, API + endpoint, timeout=5, **kwargs)

    def ids(self):
        return self.user_id.text().strip(), self.guild_id.text().strip()

    def button(self, text, fn):
        b = QPushButton(text)
        b.clicked.connect(fn)
        return b

    def sleep_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 1440)
        self.minutes.setValue(30)
        presets = QHBoxLayout()
        for text, value in [("15 min", 15), ("30 min", 30), ("45 min", 45), ("1 h", 60), ("1 h 15", 75), ("1 h 30", 90)]:
            presets.addWidget(self.button(text, lambda checked=False, v=value: self.minutes.setValue(v)))
        l.addLayout(presets)
        adj = QHBoxLayout()
        adj.addWidget(self.button("➖ Reducir", lambda: self.adjust(-5)))
        adj.addWidget(self.button("Personalizado", self.custom_time))
        adj.addWidget(self.button("➕ Extender", lambda: self.adjust(5)))
        l.addLayout(adj)
        l.addWidget(self.button("🌙 Iniciar Sleep", self.start_sleep))
        l.addWidget(self.button("❌ Cancelar temporizador", self.cancel_sleep))
        self.state_label = QLabel("⏱️ Estado del Temporizador: 🟡 Inactivo")
        l.addWidget(self.state_label)
        return w

    def schedule_tab(self):
        w = QWidget()
        l = QVBoxLayout(w)

        self.schedule_date = QDateEdit(QDate.currentDate())
        self.schedule_date.setCalendarPopup(True)
        self.schedule_date.setDisplayFormat("dd/MM/yyyy")
        self.schedule_date.setMinimumDate(QDate.currentDate())

        self.schedule_time = QTimeEdit(QTime.currentTime())
        self.schedule_time.setDisplayFormat("HH:mm")

        l.addWidget(QLabel("📅 Fecha"))
        l.addWidget(self.schedule_date)
        l.addWidget(QLabel("🕐 Hora"))
        l.addWidget(self.schedule_time)
        l.addWidget(QLabel("La desconexión se ejecutará una sola vez en esta fecha y hora."))
        l.addWidget(self.button("🌙 Guardar horario", self.create_schedule))
        l.addWidget(self.button("🔄 Actualizar", self.load_schedules))
        self.schedule_list = QListWidget()
        l.addWidget(self.schedule_list)
        return w

    def refresh(self):
        try:
            health = self.request("GET", "/api/health").json()
            self.setWindowTitle(f"XZ94 — 🟢 API | Bot listo: {health.get('bot_ready')}")
            u, g = self.ids()
            if u and g:
                s = self.request("GET", "/api/sleep/status", params={"user_id": u, "guild_id": g}).json()
                self.state_label.setText(
                    ("⏱️ Estado del Temporizador: 🟢 Activo — " if s.get("active") else "⏱️ Estado del Temporizador: 🟡 Inactivo")
                    + (f"{s.get('remaining_minutes', 0)} min" if s.get("active") else "")
                )
        except Exception:
            self.setWindowTitle("XZ94 — 🔴 API desconectada")

    def custom_time(self):
        value, ok = QInputDialog.getInt(self, "Sleep personalizado", "Minutos:", self.minutes.value(), 1, 1440)
        if ok:
            self.minutes.setValue(value)

    def start_sleep(self):
        u, g = self.ids()
        if not u or not g:
            QMessageBox.warning(self, "Datos", "Ingresa User ID y Server ID.")
            return
        r = self.request("POST", "/api/sleep/start", json={"user_id": int(u), "guild_id": int(g), "minutes": self.minutes.value()})
        d = r.json()
        if not d.get("ok"):
            QMessageBox.warning(self, "Sleep", d.get("error", "Error"))

    def adjust(self, amount):
        u, g = self.ids()
        if u and g:
            self.request("POST", "/api/sleep/adjust", json={"user_id": int(u), "guild_id": int(g), "delta": amount})

    def cancel_sleep(self):
        u, g = self.ids()
        if u and g:
            self.request("POST", "/api/sleep/cancel", json={"user_id": int(u), "guild_id": int(g)})

    def create_schedule(self):
        u, g = self.ids()
        if not u or not g:
            QMessageBox.warning(self, "Datos", "Ingresa User ID y Server ID.")
            return
        date = self.schedule_date.date().toString("yyyy-MM-dd")
        time = self.schedule_time.time().toString("HH:mm")
        r = self.request(
            "POST",
            "/api/schedules",
            json={"user_id": int(u), "guild_id": int(g), "date": date, "time": time},
        )
        d = r.json()
        if not d.get("ok"):
            QMessageBox.warning(self, "Horario", d.get("error", "Error"))
        else:
            self.load_schedules()

    def load_schedules(self):
        u, g = self.ids()
        self.schedule_list.clear()
        if not u:
            return
        try:
            d = self.request("GET", "/api/schedules", params={"user_id": u, "guild_id": g or None}).json()
            for s in d.get("schedules", []):
                status = "🟢 Activo" if s["enabled"] else "⚪ Inactivo"
                self.schedule_list.addItem(f"#{s['id']} | 📅 {s['date']} | 🕐 {s['time']} | {status}")
        except Exception:
            pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = GUI()
    window.show()
    sys.exit(app.exec())
