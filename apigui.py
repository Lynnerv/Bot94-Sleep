import sys
import requests

from PySide6.QtCore import QTimer

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QCheckBox,
    QPushButton,
    QListWidget,
    QMessageBox,
    QInputDialog
)


API = (
    "http://127.0.0.1:8765"
)


class GUI(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle(
            "Bot94 Sleep"
        )

        self.resize(
            900,
            650
        )

        self.user_id = QLineEdit()

        self.guild_id = QLineEdit()

        self.tabs = QTabWidget()

        self.tabs.addTab(
            self.sleep_tab(),
            "💤 Sleep"
        )

        self.tabs.addTab(
            self.schedule_tab(),
            "⏰ Horarios"
        )

        self.tabs.addTab(
            self.check_tab(),
            "😴 Sleep Check"
        )

        root = QWidget()

        layout = QVBoxLayout(
            root
        )

        layout.addWidget(
            QLabel(
                "Bot94 Sleep"
            )
        )

        form = QFormLayout()

        form.addRow(
            "User ID",
            self.user_id
        )

        form.addRow(
            "Server ID",
            self.guild_id
        )

        layout.addLayout(
            form
        )

        layout.addWidget(
            self.tabs
        )

        self.setCentralWidget(
            root
        )

        self.timer = QTimer(
            self
        )

        self.timer.timeout.connect(
            self.refresh
        )

        self.timer.start(
            2000
        )

        self.refresh()

    def request(
        self,
        method,
        endpoint,
        **kwargs
    ):

        return requests.request(
            method,
            API + endpoint,
            timeout=5,
            **kwargs
        )

    def ids(self):

        return (
            self.user_id.text().strip(),
            self.guild_id.text().strip()
        )

    def button(
        self,
        text,
        function
    ):

        button = QPushButton(
            text
        )

        button.clicked.connect(
            function
        )

        return button

    def sleep_tab(self):

        widget = QWidget()

        layout = QVBoxLayout(
            widget
        )

        self.minutes = QSpinBox()

        self.minutes.setRange(
            1,
            1440
        )

        self.minutes.setValue(
            30
        )

        presets = QHBoxLayout()

        for text, minutes in [
            ("15 min", 15),
            ("30 min", 30),
            ("45 min", 45),
            ("1 h", 60),
            ("1h 15", 75),
            ("1h 30", 90)
        ]:

            presets.addWidget(
                self.button(
                    text,
                    lambda checked=False,
                           value=minutes:
                        self.minutes.setValue(
                            value
                        )
                )
            )

        layout.addLayout(
            presets
        )

        adjustments = QHBoxLayout()

        adjustments.addWidget(
            self.button(
                "➖ 15",
                lambda:
                    self.adjust(-15)
            )
        )

        adjustments.addWidget(
            self.button(
                "✏️ Personalizado",
                self.custom_time
            )
        )

        adjustments.addWidget(
            self.button(
                "➕ 15",
                lambda:
                    self.adjust(15)
            )
        )

        layout.addLayout(
            adjustments
        )

        self.mute = QCheckBox(
            "🔇 Mute"
        )

        self.deafen = QCheckBox(
            "🎧 Deafen"
        )

        self.disconnect = QCheckBox(
            "🚪 Disconnect"
        )

        self.deafen.stateChanged.connect(
            lambda state:
                self.mute.setChecked(
                    bool(state)
                )
        )

        layout.addWidget(
            self.mute
        )

        layout.addWidget(
            self.deafen
        )

        layout.addWidget(
            self.disconnect
        )

        layout.addWidget(
            self.button(
                "▶ Iniciar Sleep",
                self.start_sleep
            )
        )

        layout.addWidget(
            self.button(
                "⏹ Cancelar",
                self.cancel_sleep
            )
        )

        self.state_label = QLabel(
            "Sleep: idle"
        )

        layout.addWidget(
            self.state_label
        )

        return widget

    def schedule_tab(self):

        widget = QWidget()

        layout = QVBoxLayout(
            widget
        )

        self.schedule_time = QLineEdit(
            "23:59"
        )

        self.schedule_days = QLineEdit(
            "lunes,martes,miercoles,jueves,viernes"
        )

        self.schedule_confirmation = QCheckBox(
            "🔔 Pedir confirmación"
        )

        self.schedule_confirmation.setChecked(
            True
        )

        self.schedule_mute = QCheckBox(
            "🔇 Mute"
        )

        self.schedule_deafen = QCheckBox(
            "🎧 Deafen"
        )

        self.schedule_disconnect = QCheckBox(
            "🚪 Disconnect"
        )

        self.schedule_deafen.stateChanged.connect(
            lambda state:
                self.schedule_mute.setChecked(
                    bool(state)
                )
        )

        layout.addWidget(
            QLabel(
                "Hora HH:MM"
            )
        )

        layout.addWidget(
            self.schedule_time
        )

        layout.addWidget(
            QLabel(
                "Días separados por coma"
            )
        )

        layout.addWidget(
            self.schedule_days
        )

        layout.addWidget(
            self.schedule_confirmation
        )

        layout.addWidget(
            self.schedule_mute
        )

        layout.addWidget(
            self.schedule_deafen
        )

        layout.addWidget(
            self.schedule_disconnect
        )

        layout.addWidget(
            self.button(
                "➕ Crear horario",
                self.create_schedule
            )
        )

        layout.addWidget(
            self.button(
                "🔄 Actualizar",
                self.load_schedules
            )
        )

        self.schedule_list = QListWidget()

        layout.addWidget(
            self.schedule_list
        )

        return widget

    def check_tab(self):

        widget = QWidget()

        layout = QVBoxLayout(
            widget
        )

        self.check_enabled = QCheckBox(
            "😴 Activar Sleep Check"
        )

        self.check_interval = QSpinBox()

        self.check_interval.setRange(
            1,
            1440
        )

        self.check_interval.setValue(
            15
        )

        self.check_response = QSpinBox()

        self.check_response.setRange(
            1,
            30
        )

        self.check_response.setValue(
            2
        )

        layout.addWidget(
            self.check_enabled
        )

        layout.addWidget(
            QLabel(
                "Preguntar cada (min)"
            )
        )

        layout.addWidget(
            self.check_interval
        )

        layout.addWidget(
            QLabel(
                "Esperar respuesta (min)"
            )
        )

        layout.addWidget(
            self.check_response
        )

        layout.addWidget(
            self.button(
                "💾 Guardar",
                self.save_sleep_check
            )
        )

        layout.addWidget(
            QLabel(
                "Si no respondes, Bot94 Sleep "
                "te silenciará. El horario de "
                "desconexión sigue funcionando "
                "independientemente."
            )
        )

        return widget

    def refresh(self):

        try:

            health = self.request(
                "GET",
                "/api/health"
            ).json()

            self.setWindowTitle(
                "Bot94 Sleep — "
                "🟢 API | Bot listo: "
                +
                str(
                    health.get(
                        "bot_ready"
                    )
                )
            )

            user_id, guild_id = self.ids()

            if user_id and guild_id:

                status = self.request(
                    "GET",
                    "/api/sleep/status",
                    params={
                        "user_id":
                            user_id,

                        "guild_id":
                            guild_id
                    }
                ).json()

                self.state_label.setText(
                    "Sleep: "
                    +
                    str(
                        status.get(
                            "status"
                        )
                    )
                    +
                    " | "
                    +
                    str(
                        status.get(
                            "remaining_minutes",
                            0
                        )
                    )
                    +
                    " min"
                )

        except Exception:

            self.setWindowTitle(
                "Bot94 Sleep — "
                "🔴 API desconectada"
            )

    def custom_time(self):

        value, accepted = QInputDialog.getInt(
            self,
            "Sleep personalizado",
            "Minutos:",
            self.minutes.value(),
            1,
            1440
        )

        if accepted:

            self.minutes.setValue(
                value
            )

    def start_sleep(self):

        user_id, guild_id = self.ids()

        if not user_id or not guild_id:

            QMessageBox.warning(
                self,
                "Datos",
                "Ingresa User ID y Server ID."
            )

            return

        response = self.request(
            "POST",
            "/api/sleep/start",
            json={
                "user_id":
                    int(user_id),

                "guild_id":
                    int(guild_id),

                "minutes":
                    self.minutes.value(),

                "mute":
                    self.mute.isChecked(),

                "deafen":
                    self.deafen.isChecked(),

                "disconnect":
                    self.disconnect.isChecked()
            }
        )

        data = response.json()

        if not data.get("ok"):

            QMessageBox.warning(
                self,
                "Sleep",
                data.get(
                    "error",
                    "Error"
                )
            )

    def adjust(
        self,
        amount
    ):

        user_id, guild_id = self.ids()

        if (
            user_id
            and guild_id
        ):

            self.request(
                "POST",
                "/api/sleep/adjust",
                json={
                    "user_id":
                        int(user_id),

                    "guild_id":
                        int(guild_id),

                    "delta":
                        amount
                }
            )

    def cancel_sleep(self):

        user_id, guild_id = self.ids()

        if (
            user_id
            and guild_id
        ):

            self.request(
                "POST",
                "/api/sleep/cancel",
                json={
                    "user_id":
                        int(user_id),

                    "guild_id":
                        int(guild_id)
                }
            )

    def create_schedule(self):

        user_id, guild_id = self.ids()

        if (
            not user_id
            or not guild_id
        ):

            QMessageBox.warning(
                self,
                "Datos",
                "Ingresa User ID y Server ID."
            )

            return

        days = [
            day.strip().lower()
            for day
            in
            self.schedule_days.text().split(",")
            if day.strip()
        ]

        response = self.request(
            "POST",
            "/api/schedules",
            json={
                "user_id":
                    int(user_id),

                "guild_id":
                    int(guild_id),

                "time":
                    self.schedule_time.text(),

                "days":
                    days,

                "mute":
                    self.schedule_mute.isChecked(),

                "deafen":
                    self.schedule_deafen.isChecked(),

                "disconnect":
                    self.schedule_disconnect.isChecked(),

                "confirmation":
                    self.schedule_confirmation.isChecked()
            }
        )

        data = response.json()

        if not data.get("ok"):

            QMessageBox.warning(
                self,
                "Horario",
                data.get(
                    "error",
                    "Error"
                )
            )

        self.load_schedules()

    def load_schedules(self):

        user_id, guild_id = self.ids()

        if not user_id:

            return

        try:

            response = self.request(
                "GET",
                "/api/schedules",
                params={
                    "user_id":
                        int(user_id),

                    "guild_id":
                        int(guild_id)
                    if guild_id
                    else None
                }
            )

            data = response.json()

        except Exception:

            return

        self.schedule_list.clear()

        for schedule in data.get(
            "schedules",
            []
        ):

            self.schedule_list.addItem(
                f"#{schedule['id']} "
                f"{schedule['time']} | "
                f"{', '.join(schedule['days'])} | "
                f"M:{schedule['mute']} "
                f"D:{schedule['deafen']} "
                f"X:{schedule['disconnect']}"
            )

    def save_sleep_check(self):

        user_id, guild_id = self.ids()

        if (
            not user_id
            or not guild_id
        ):

            QMessageBox.warning(
                self,
                "Datos",
                "Ingresa User ID y Server ID."
            )

            return

        response = self.request(
            "POST",
            "/api/sleep-check",
            json={
                "user_id":
                    int(user_id),

                "guild_id":
                    int(guild_id),

                "enabled":
                    self.check_enabled.isChecked(),

                "interval_minutes":
                    self.check_interval.value(),

                "response_minutes":
                    self.check_response.value()
            }
        )

        data = response.json()

        if not data.get("ok"):

            QMessageBox.warning(
                self,
                "Sleep Check",
                data.get(
                    "error",
                    "Error"
                )
            )


if __name__ == "__main__":

    app = QApplication(
        sys.argv
    )

    window = GUI()

    window.show()

    sys.exit(
        app.exec()
    )