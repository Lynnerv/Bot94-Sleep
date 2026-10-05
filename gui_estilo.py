import sys
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTime, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from database import (
    init_database,
    create_schedule,
    get_user_schedules,
    update_schedule,
    delete_schedule,
    set_schedule_enabled,
)

APP_TITLE = "Bot94 Sleep"
TIMEZONE_TEXT = "America/Lima"

class FlexibleTimeEdit(QTimeEdit):
    """
    Campo de hora 24h:
    - Se puede seleccionar/borrar todo.
    - 8   -> 08:00
    - 839 -> 08:39
    - 2230 -> 22:30
    - Sin flechas visibles.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._input_buffer = ""
        self.setDisplayFormat("HH:mm")
        self.setKeyboardTracking(False)
        self.lineEdit().setReadOnly(False)
        self.lineEdit().setAlignment(Qt.AlignCenter)

    def _commit_buffer(self):
        if not self._input_buffer:
            return True

        raw = self._input_buffer

        try:
            if len(raw) <= 2:
                hour = int(raw)
                minute = 0
            elif len(raw) == 3:
                hour = int(raw[0])
                minute = int(raw[1:])
            elif len(raw) == 4:
                hour = int(raw[:2])
                minute = int(raw[2:])
            else:
                return False

            if 0 <= hour <= 23 and 0 <= minute <= 59:
                self.setTime(QTime(hour, minute))
                self._input_buffer = ""
                return True
        except ValueError:
            pass

        return False

    def _clear_input(self):
        self._input_buffer = ""
        self.lineEdit().clear()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._input_buffer = ""
        self.lineEdit().selectAll()

    def focusOutEvent(self, event):
        if self._input_buffer:
            self._commit_buffer()
        else:
            # Si el usuario borró todo, mantenemos la última hora válida
            # para que el control nunca quede en un estado inválido.
            if not self.lineEdit().text().strip():
                self.lineEdit().setText(self.time().toString("HH:mm"))

        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        key = event.key()

        # Dígitos: acumular sin auto-rellenar cada pulsación.
        if Qt.Key_0 <= key <= Qt.Key_9:
            digit = event.text()
            if digit:
                self._input_buffer += digit

                # 3 dígitos = HMM (ej. 839 -> 08:39)
                # 4 dígitos = HHMM (ej. 2230 -> 22:30)
                if len(self._input_buffer) in (3, 4):
                    if self._commit_buffer():
                        return

                # Mientras se escribe, mostrar exactamente lo introducido.
                self.lineEdit().setText(self._input_buffer)
                self.lineEdit().setCursorPosition(len(self._input_buffer))
                return

        # Backspace/Delete permiten borrar todo.
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            if self._input_buffer:
                self._input_buffer = self._input_buffer[:-1]
                self.lineEdit().setText(self._input_buffer)
            else:
                self._clear_input()
            return

        # Enter confirma la hora escrita.
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._input_buffer:
                self._commit_buffer()
            return

        # Flechas izquierda/derecha normales.
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Home, Qt.Key_End):
            super().keyPressEvent(event)
            return

        # Arriba/abajo: +/- 1 minuto.
        if key in (Qt.Key_Up, Qt.Key_Down):
            if self._input_buffer:
                self._commit_buffer()
            self.stepBy(1 if key == Qt.Key_Up else -1)
            return

        super().keyPressEvent(event)

    def stepBy(self, steps):
        # Las flechas y los botones de flecha del QTimeEdit usan esto.
        if self._input_buffer:
            self._commit_buffer()

        self.setTime(self.time().addSecs(steps * 60))


DAY_NAMES = [
    ("L", "lunes"),
    ("M", "martes"),
    ("X", "miercoles"),
    ("J", "jueves"),
    ("V", "viernes"),
    ("S", "sabado"),
    ("D", "domingo"),
]


def normalize_actions(mute, deafen, disconnect):
    # Deafen always implies Mute.
    if deafen:
        mute = True

    return mute, deafen, disconnect


def actions_text(row):
    actions = []
    if row["mute"]:
        actions.append("🔇 Mute")
    if row["deafen"]:
        actions.append("🎧 Deafen")
    if row["disconnect"]:
        actions.append("🚪 Disconnect")
    return " + ".join(actions) if actions else "Sin acciones"


class ScheduleDialog(QDialog):
    def __init__(self, parent=None, schedule=None):
        super().__init__(parent)
        self.schedule = schedule

        self.setWindowTitle(
            "NUEVO HORARIO" if schedule is None else "EDITAR HORARIO"
        )
        self.setFixedSize(640, 555)
        self.setObjectName("ScheduleDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(15)

        # Header
        header = QVBoxLayout()
        header.setSpacing(3)

        eyebrow = QLabel("BOT94  •  SLEEP SCHEDULE")
        eyebrow.setObjectName("DialogEyebrow")
        header.addWidget(eyebrow)

        title = QLabel(
            "Nuevo horario" if schedule is None else "Editar horario"
        )
        title.setObjectName("DialogTitle")
        header.addWidget(title)

        subtitle = QLabel(
            "Configura cuándo y qué acción debe realizar Bot94 Sleep."
        )
        subtitle.setObjectName("DialogSubtitle")
        header.addWidget(subtitle)

        layout.addLayout(header)

        # Time card
        time_card = QFrame()
        time_card.setObjectName("DialogCard")
        time_layout = QHBoxLayout(time_card)
        time_layout.setContentsMargins(16, 11, 16, 11)

        time_text = QVBoxLayout()
        time_text.setSpacing(2)

        time_label = QLabel("HORA DE EJECUCIÓN")
        time_label.setObjectName("DialogSection")
        time_text.addWidget(time_label)

        time_hint = QLabel("Hora local · formato 24 horas")
        time_hint.setObjectName("DialogHint")
        time_text.addWidget(time_hint)

        time_layout.addLayout(time_text)
        time_layout.addStretch()

        self.time_edit = FlexibleTimeEdit()
        self.time_edit.setObjectName("ScheduleTime")
        self.time_edit.setTime(QTime.currentTime())
        self.time_edit.setButtonSymbols(QTimeEdit.ButtonSymbols.NoButtons)
        time_layout.addWidget(self.time_edit)

        layout.addWidget(time_card)

        # Days
        days_header = QHBoxLayout()
        days_header.setAlignment(Qt.AlignHCenter)

        days_label = QLabel("DÍAS")
        days_label.setObjectName("DialogSection")
        days_header.addWidget(days_label)

        days_hint = QLabel("   ·   Selecciona uno o varios")
        days_hint.setObjectName("DialogHint")
        days_header.addWidget(days_hint)

        layout.addLayout(days_header)

        days_layout = QHBoxLayout()
        days_layout.setSpacing(9)
        days_layout.setAlignment(Qt.AlignHCenter)

        self.day_checks = {}

        for short, full in DAY_NAMES:
            check = QPushButton(short.upper())
            check.setCheckable(True)
            check.setObjectName("DayCheck")
            check.setToolTip(full.capitalize())
            check.setFixedSize(68, 44)
            check.setCursor(Qt.PointingHandCursor)
            self.day_checks[full] = check
            days_layout.addWidget(check)

        layout.addLayout(days_layout)

        # Actions
        actions_label = QLabel("ACCIONES")
        actions_label.setObjectName("DialogSection")
        actions_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(actions_label)

        actions_layout = QGridLayout()
        actions_layout.setHorizontalSpacing(10)
        actions_layout.setVerticalSpacing(10)

        self.mute_check = QCheckBox("🔇   Mute")
        self.deafen_check = QCheckBox("🎧   Deafen")
        self.disconnect_check = QCheckBox("🚪   Disconnect")
        self.confirmation_check = QCheckBox("◉   Pedir confirmación")

        for check in (
            self.mute_check,
            self.deafen_check,
            self.disconnect_check,
            self.confirmation_check,
        ):
            check.setObjectName("ActionCheck")
            check.setMinimumHeight(46)

        self.deafen_check.toggled.connect(self.on_deafen_changed)

        actions_layout.addWidget(self.mute_check, 0, 0)
        actions_layout.addWidget(self.deafen_check, 0, 1)
        actions_layout.addWidget(self.disconnect_check, 1, 0)
        actions_layout.addWidget(self.confirmation_check, 2, 0, 1, 2)

        layout.addLayout(actions_layout)

        info = QLabel(
            "ℹ  Deafen incluye Mute."
        )
        info.setObjectName("DialogInfo")
        info.setAlignment(Qt.AlignCenter)
        layout.addWidget(info)

        layout.addStretch()

        # Footer
        buttons = QHBoxLayout()
        buttons.setSpacing(9)

        cancel_button = QPushButton("Cancelar")
        cancel_button.setObjectName("DialogCancel")
        cancel_button.setMinimumHeight(38)
        cancel_button.clicked.connect(self.reject)

        save_button = QPushButton("Guardar horario")
        save_button.setObjectName("DialogSave")
        save_button.setMinimumHeight(38)
        save_button.clicked.connect(self.accept)

        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)

        layout.addLayout(buttons)

        if schedule is not None:
            self.load_schedule(schedule)

    def on_deafen_changed(self, checked):
        if checked:
            self.mute_check.setChecked(True)

    def load_schedule(self, row):
        hour, minute = map(int, row["time"].split(":"))
        self.time_edit.setTime(QTime(hour, minute))

        saved_days = {
            x.strip().lower()
            for x in row["days"].split(",")
            if x.strip()
        }

        for full, check in self.day_checks.items():
            check.setChecked(full in saved_days)

        self.mute_check.setChecked(bool(row["mute"]))
        self.deafen_check.setChecked(bool(row["deafen"]))
        self.disconnect_check.setChecked(bool(row["disconnect"]))
        self.confirmation_check.setChecked(bool(row["confirmation"]))

    def get_values(self):
        selected_days = [
            full for _, full in DAY_NAMES
            if self.day_checks[full].isChecked()
        ]

        mute = self.mute_check.isChecked()
        deafen = self.deafen_check.isChecked()
        disconnect = self.disconnect_check.isChecked()

        mute, deafen, disconnect = normalize_actions(
            mute, deafen, disconnect
        )

        return {
            "time": self.time_edit.time().toString("HH:mm"),
            "days": ",".join(selected_days),
            "mute": mute,
            "deafen": deafen,
            "disconnect": disconnect,
            "confirmation": self.confirmation_check.isChecked(),
        }

    def accept(self):
        values = self.get_values()

        if not values["days"]:
            QMessageBox.warning(
                self,
                "Faltan días",
                "Selecciona al menos un día."
            )
            return

        if not any(
            [
                values["mute"],
                values["deafen"],
                values["disconnect"],
            ]
        ):
            QMessageBox.warning(
                self,
                "Falta una acción",
                "Selecciona al menos una acción."
            )
            return

        super().accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1120, 720)
        self.setMinimumSize(1000, 650)

        self.user_id = ""
        self.guild_id = ""


        self.setStyleSheet("""
            QMainWindow {
                background: #060e1f;
            }

            QWidget {
                color: #dcecff;
                font-family: "Segoe UI";
            }

            QFrame#Sidebar {
                background: #071426;
                border-right: 1px solid #1e4a8a;
            }

            QLabel#Logo {
                color: #e8f4ff;
                font-size: 21px;
                font-weight: 700;
                padding: 12px 10px;
            }

            QPushButton#NavButton {
                color: #7ab0d8;
                background: transparent;
                border: 1px solid transparent;
                text-align: left;
                padding: 12px 14px;
                font-size: 13px;
                font-weight: 600;
                border-radius: 7px;
            }

            QPushButton#NavButton:hover {
                background: #10294c;
                border-color: #28558c;
                color: #d0eaff;
            }

            QPushButton#NavButton:checked {
                background: #063d42;
                border-color: #00d4a0;
                color: #e8fff9;
            }

            QLabel#Title {
                color: #e8f4ff;
                font-size: 28px;
                font-weight: 700;
                letter-spacing: 1px;
            }

            QLabel#Subtitle {
                color: #6e9cc4;
                font-size: 13px;
            }

            QFrame#Card {
                background: #0a192e;
                border: 1px solid #1e4a8a;
                border-radius: 12px;
            }

            QFrame#Card:hover {
                border-color: #2e72ad;
            }

            QLabel#CardTitle {
                color: #e8f4ff;
                font-size: 15px;
                font-weight: 700;
            }

            QLabel#CardText {
                color: #83acd0;
                font-size: 12px;
            }

            QLabel#SectionLabel {
                color: #4ab3e0;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }

            QPushButton#Primary {
                background: #00cfa0;
                color: #03251d;
                border: 1px solid #00e8b0;
                border-radius: 7px;
                padding: 9px 16px;
                font-weight: 700;
            }

            QPushButton#Primary:hover {
                background: #00e8b0;
            }

            QPushButton#Secondary {
                background: #10294c;
                color: #b8d8f0;
                border: 1px solid #2a5aaa;
                border-radius: 7px;
                padding: 8px 14px;
                font-weight: 600;
            }

            QPushButton#Secondary:hover {
                background: #173761;
                border-color: #4a8adc;
            }

            QPushButton#Danger {
                background: #4b1f32;
                color: #ffb8c8;
                border: 1px solid #9a3b58;
                border-radius: 7px;
                padding: 8px 14px;
                font-weight: 700;
            }

            QPushButton#Danger:hover {
                background: #67263e;
            }

            QLineEdit, QSpinBox, QTimeEdit, QComboBox {
                background: #071426;
                color: #dcecff;
                border: 1px solid #234d7e;
                border-radius: 7px;
                padding: 8px;
                selection-background-color: #00b894;
            }

            QLineEdit:focus, QSpinBox:focus, QTimeEdit:focus, QComboBox:focus {
                border-color: #00cfa0;
            }

            QCheckBox {
                color: #a8c8e4;
                spacing: 8px;
                padding: 4px;
            }

            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #2a5aaa;
                background: #071426;
            }

            QCheckBox::indicator:checked {
                background: #00cfa0;
                border-color: #00e8b0;
            }

            QListWidget {
                background: #08172a;
                color: #b9d7ed;
                border: 1px solid #1e4a8a;
                border-radius: 10px;
                outline: none;
            }

            QListWidget::item {
                padding: 12px;
                border-bottom: 1px solid #122d4d;
            }

            QListWidget::item:hover {
                background: #10294c;
            }

            QListWidget::item:selected {
                background: #073d43;
                color: #e8fff9;
                border-left: 2px solid #00d4a0;
            }

            QDialog {
                background: #071426;
            }

            QDialog#ScheduleDialog {
                background: #060e1f;
            }

            QLabel#DialogEyebrow {
                color: #4ab3e0;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 2px;
            }

            QLabel#DialogTitle {
                color: #e8f4ff;
                font-size: 26px;
                font-weight: 700;
            }

            QLabel#DialogSubtitle {
                color: #6e9cc4;
                font-size: 12px;
            }

            QFrame#DialogCard {
                background: #0a192e;
                border: 1px solid #1e4a8a;
                border-radius: 10px;
            }

            QLabel#DialogSection {
                color: #4ab3e0;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1.5px;
            }

            QLabel#DialogHint {
                color: #557d9f;
                font-size: 10px;
            }

            QTimeEdit#ScheduleTime {
                background: #071426;
                color: #e8f4ff;
                border: 1px solid #00bfa0;
                border-radius: 7px;
                padding: 7px 10px;
                font-size: 18px;
                font-weight: 700;
                min-width: 118px;
            }

            QPushButton#DayCheck {
                background: #0a192e;
                color: #7fa9ca;
                border: 1px solid #234d7e;
                border-radius: 7px;
                font-size: 13px;
                font-weight: 700;
                padding: 0;
            }

            QPushButton#DayCheck:hover {
                background: #10294c;
                border-color: #4a8adc;
                color: #d0eaff;
            }

            QPushButton#DayCheck:checked {
                background: #063d42;
                border: 1px solid #00d4a0;
                color: #e8fff9;
            }

            QPushButton#DayCheck:pressed {
                background: #073d43;
            }

            QCheckBox#ActionCheck {
                background: #0a192e;
                color: #a8c8e4;
                border: 1px solid #1e4a8a;
                border-radius: 8px;
                padding: 0 13px;
                font-size: 12px;
                font-weight: 600;
            }

            QCheckBox#ActionCheck:hover {
                background: #10294c;
                border-color: #2f73aa;
            }

            QCheckBox#ActionCheck:checked {
                background: #073d43;
                border-color: #00d4a0;
                color: #e8fff9;
            }

            QCheckBox#ActionCheck::indicator {
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid #315f8e;
                background: #071426;
            }

            QCheckBox#ActionCheck::indicator:checked {
                background: #00cfa0;
                border-color: #00e8b0;
            }

            QLabel#DialogInfo {
                color: #6c96b7;
                background: #08172a;
                border: 1px solid #15385d;
                border-radius: 7px;
                padding: 8px 10px;
                font-size: 10px;
            }

            QPushButton#DialogCancel {
                background: #0c1d33;
                color: #8fb4d2;
                border: 1px solid #234d7e;
                border-radius: 7px;
                padding: 9px 18px;
                font-weight: 600;
            }

            QPushButton#DialogCancel:hover {
                background: #10294c;
                border-color: #4a8adc;
                color: #d0eaff;
            }

            QPushButton#DialogSave {
                background: #00cfa0;
                color: #03251d;
                border: 1px solid #00e8b0;
                border-radius: 7px;
                padding: 9px 20px;
                font-weight: 700;
            }

            QPushButton#DialogSave:hover {
                background: #00e8b0;
            }


            QDialog#ScheduleDialog {
                background: #060e1f;
            }

            QLabel#DialogEyebrow {
                color: #4ab3e0;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 2px;
            }

            QLabel#DialogTitle {
                color: #e8f4ff;
                font-size: 25px;
                font-weight: 700;
            }

            QLabel#DialogSubtitle {
                color: #6e9cc4;
                font-size: 12px;
            }

            QFrame#DialogCard {
                background: #0a192e;
                border: 1px solid #1e4a8a;
                border-radius: 10px;
            }

            QLabel#DialogSection {
                color: #4ab3e0;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1.5px;
            }

            QLabel#DialogHint {
                color: #557d9f;
                font-size: 10px;
            }

            QTimeEdit#ScheduleTime {
                background: #071426;
                color: #e8f4ff;
                border: 1px solid #00bfa0;
                border-radius: 7px;
                padding: 6px 9px;
                font-size: 17px;
                font-weight: 700;
                min-width: 92px;
            }

            QPushButton#DayCheck {
                background: #0a192e;
                color: #7fa9ca;
                border: 1px solid #234d7e;
                border-radius: 7px;
                font-size: 13px;
                font-weight: 700;
                padding: 0;
            }

            QPushButton#DayCheck:hover {
                background: #10294c;
                border-color: #4a8adc;
                color: #d0eaff;
            }

            QPushButton#DayCheck:checked {
                background: #063d42;
                border: 1px solid #00d4a0;
                color: #e8fff9;
            }

            QPushButton#DayCheck:pressed {
                background: #073d43;
            }

            QCheckBox#ActionCheck {
                background: #0a192e;
                color: #a8c8e4;
                border: 1px solid #1e4a8a;
                border-radius: 8px;
                padding: 0 12px;
                font-size: 12px;
                font-weight: 600;
            }

            QCheckBox#ActionCheck:hover {
                background: #10294c;
                border-color: #2f73aa;
            }

            QCheckBox#ActionCheck:checked {
                background: #073d43;
                border-color: #00d4a0;
                color: #e8fff9;
            }

            QCheckBox#ActionCheck::indicator {
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid #315f8e;
                background: #071426;
            }

            QCheckBox#ActionCheck::indicator:checked {
                background: #00cfa0;
                border-color: #00e8b0;
            }

            QLabel#DialogInfo {
                color: #5f88a8;
                background: #08172a;
                border: 1px solid #15385d;
                border-radius: 7px;
                padding: 8px 10px;
                font-size: 10px;
            }

            QPushButton#DialogCancel {
                background: #0c1d33;
                color: #8fb4d2;
                border: 1px solid #234d7e;
                border-radius: 7px;
                padding: 9px 17px;
                font-weight: 600;
            }

            QPushButton#DialogCancel:hover {
                background: #10294c;
                border-color: #4a8adc;
                color: #d0eaff;
            }

            QPushButton#DialogSave {
                background: #00cfa0;
                color: #03251d;
                border: 1px solid #00e8b0;
                border-radius: 7px;
                padding: 9px 18px;
                font-weight: 700;
            }

            QPushButton#DialogSave:hover {
                background: #00e8b0;
            }


            QDialogButtonBox QPushButton {
                background: #10294c;
                color: #cce6f8;
                border: 1px solid #2a5aaa;
                border-radius: 6px;
                padding: 7px 15px;
            }

            QDialogButtonBox QPushButton:hover {
                background: #173761;
            }

            QScrollBar:vertical {
                background: #071426;
                width: 10px;
                margin: 2px;
            }

            QScrollBar::handle:vertical {
                background: #1e4a8a;
                border-radius: 5px;
                min-height: 30px;
            }

            QScrollBar::handle:vertical:hover {
                background: #2f73aa;
            }
        """)

        self.build_ui()
        self.load_settings()
        self.refresh_schedules()

    def build_ui(self):
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 18, 12, 12)

        logo = QLabel("🌙  BOT94 SLEEP")
        logo.setObjectName("Logo")
        side_layout.addWidget(logo)

        self.nav_buttons = []
        pages = [
            ("🏠  Inicio", 0),
            ("⏰  Sleep", 1),
            ("📅  Horarios", 2),
            ("⚙️  Ajustes", 3),
        ]

        for text, index in pages:
            button = QPushButton(text)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked, i=index: self.show_page(i))
            side_layout.addWidget(button)
            self.nav_buttons.append(button)

        side_layout.addStretch()

        version = QLabel("BOT94 SLEEP  •  v1.1.4")
        version.setStyleSheet("color: #888; padding: 8px;")
        side_layout.addWidget(version)

        root_layout.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.build_home_page())
        self.stack.addWidget(self.build_sleep_page())
        self.stack.addWidget(self.build_schedules_page())
        self.stack.addWidget(self.build_settings_page())

        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.show_page(0)

    def page_container(self, title, subtitle):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(16)

        title_label = QLabel(title)
        title_label.setObjectName("Title")
        layout.addWidget(title_label)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("Subtitle")
        layout.addWidget(subtitle_label)

        return page, layout

    def build_home_page(self):
        page, layout = self.page_container(
            "Inicio",
            "Centro de control de Bot94 Sleep."
        )

        hero = QFrame()
        hero.setObjectName("Card")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(22, 20, 22, 20)

        eyebrow = QLabel("BOT94  •  SLEEP CONTROL")
        eyebrow.setObjectName("SectionLabel")
        hero_layout.addWidget(eyebrow)

        welcome = QLabel("Descansa. Nosotros nos encargamos.")
        welcome.setStyleSheet(
            "color: #e8f4ff; font-size: 24px; font-weight: 700;"
        )
        hero_layout.addWidget(welcome)

        hero_text = QLabel(
            "Programa acciones de voz, administra horarios recurrentes "
            "y controla tu sesión de Discord desde un solo lugar."
        )
        hero_text.setObjectName("CardText")
        hero_text.setWordWrap(True)
        hero_layout.addWidget(hero_text)

        layout.addWidget(hero)

        grid = QGridLayout()
        grid.setSpacing(12)

        bot_card = self.make_card(
            "◉  Estado del bot",
            "🟢 Configuración local lista\n"
            "El proceso de bot.py debe estar ejecutándose."
        )

        server_card = self.make_card(
            "◇  Conexión",
            "Servidor: pendiente\n"
            "Usuario: pendiente"
        )
        self.home_config_card = server_card

        timezone_card = self.make_card(
            "◷  Zona horaria",
            TIMEZONE_TEXT
        )

        schedules_card = self.make_card(
            "▦  Horarios",
            "0 horarios configurados"
        )
        self.home_schedules_card = schedules_card

        grid.addWidget(bot_card, 0, 0)
        grid.addWidget(server_card, 0, 1)
        grid.addWidget(timezone_card, 1, 0)
        grid.addWidget(schedules_card, 1, 1)

        layout.addLayout(grid)

        hint = QLabel(
            "▶  Consejo: configura tu User ID y Server ID en Ajustes "
            "antes de crear un horario."
        )
        hint.setStyleSheet(
            "color: #4ab3e0; font-size: 11px; padding: 8px 2px;"
        )
        layout.addWidget(hint)

        layout.addStretch()
        return page

    def make_card(self, title, text):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)

        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")

        text_label = QLabel(text)
        text_label.setObjectName("CardText")
        text_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(text_label)

        card._text_label = text_label
        return card

    def build_sleep_page(self):
        page, layout = self.page_container(
            "Sleep",
            "Prepara un temporizador de una sola ejecución."
        )

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 22, 22, 22)
        card_layout.setSpacing(12)

        label = QLabel("Tiempo del Sleep")
        label.setStyleSheet("font-weight: 700; font-size: 15px;")
        card_layout.addWidget(label)

        self.sleep_minutes = QSpinBox()
        self.sleep_minutes.setRange(1, 1440)
        self.sleep_minutes.setValue(15)
        self.sleep_minutes.setSuffix(" min")
        self.sleep_minutes.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.sleep_minutes.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sleep_minutes.setMinimumHeight(42)
        card_layout.addWidget(self.sleep_minutes)

        self.sleep_time_label = QLabel("⏰ 15 min")
        self.sleep_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sleep_time_label.setStyleSheet(
            "color: #00d4a0; font-size: 18px; font-weight: 700;"
        )
        card_layout.addWidget(self.sleep_time_label)

        presets = QGridLayout()
        presets.setHorizontalSpacing(8)
        presets.setVerticalSpacing(8)

        preset_values = [
            ("15 min", 15),
            ("30 min", 30),
            ("45 min", 45),
            ("1 hora", 60),
            ("1h 15m", 75),
            ("1h 30m", 90),
        ]

        for index, (label_text, value) in enumerate(preset_values):
            button = QPushButton(label_text)
            button.setObjectName("Secondary")
            button.setMinimumHeight(38)
            button.clicked.connect(
                lambda checked=False, v=value: self.set_sleep_minutes(v)
            )
            presets.addWidget(button, index // 3, index % 3)

        card_layout.addLayout(presets)

        adjust = QHBoxLayout()
        adjust.setSpacing(8)

        minus_button = QPushButton("➖ 15 min")
        minus_button.setObjectName("Secondary")
        minus_button.clicked.connect(
            lambda: self.adjust_sleep_minutes(-15)
        )

        custom_button = QPushButton("✏️ Personalizado")
        custom_button.setObjectName("Secondary")
        custom_button.clicked.connect(self.focus_sleep_minutes)

        plus_button = QPushButton("➕ 15 min")
        plus_button.setObjectName("Secondary")
        plus_button.clicked.connect(
            lambda: self.adjust_sleep_minutes(15)
        )

        adjust.addWidget(minus_button)
        adjust.addWidget(custom_button)
        adjust.addWidget(plus_button)
        card_layout.addLayout(adjust)

        card_layout.addSpacing(8)

        actions_label = QLabel("Acciones")
        actions_label.setStyleSheet("font-weight: 700;")
        card_layout.addWidget(actions_label)

        self.sleep_mute = QCheckBox("🔇 Mute")
        self.sleep_deafen = QCheckBox("🎧 Deafen")
        self.sleep_disconnect = QCheckBox("🚪 Disconnect")
        self.sleep_confirmation = QCheckBox("◉ Pedir confirmación")
        self.sleep_confirmation.setChecked(True)

        self.sleep_deafen.toggled.connect(
            lambda checked: self.sleep_mute.setChecked(True)
            if checked else None
        )

        card_layout.addWidget(self.sleep_mute)
        card_layout.addWidget(self.sleep_deafen)
        card_layout.addWidget(self.sleep_disconnect)
        card_layout.addWidget(self.sleep_confirmation)

        info = QLabel(
            "Los tiempos rápidos solo preparan la configuración del Sleep. "
            "La ejecución del temporizador desde la GUI se conectará al bot "
            "cuando integremos la comunicación GUI ↔ bot. Por ahora, "
            "puedes ejecutar /sleep directamente desde Discord."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #6e9cc4;")
        card_layout.addWidget(info)

        button = QPushButton("🌙 Iniciar Sleep")
        button.setObjectName("Primary")
        button.clicked.connect(self.sleep_not_ready)
        card_layout.addWidget(
            button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

        self.sleep_minutes.valueChanged.connect(
            self.update_sleep_time_label
        )

        layout.addWidget(card)
        layout.addStretch()

        return page

    def update_sleep_time_label(self, value):
        hours, minutes = divmod(int(value), 60)

        if hours == 0:
            text = f"{value} min"
        elif minutes == 0:
            text = f"{hours} h"
        else:
            text = f"{hours} h {minutes} min"

        self.sleep_time_label.setText(f"⏰ {text}")

    def set_sleep_minutes(self, value):
        self.sleep_minutes.setValue(
            max(1, min(1440, int(value)))
        )

    def adjust_sleep_minutes(self, delta):
        self.set_sleep_minutes(
            self.sleep_minutes.value() + delta
        )

    def focus_sleep_minutes(self):
        self.sleep_minutes.setFocus()
        self.sleep_minutes.selectAll()

    def sleep_not_ready(self):
        QMessageBox.information(
            self,
            "Sleep",
            "La configuración del Sleep está lista.\n\n"
            "La ejecución directa desde la GUI todavía no está conectada "
            "al bot. Mientras tanto, usa /sleep desde Discord."
        )

    def build_schedules_page(self):
        page, layout = self.page_container(
            "Horarios",
            "Crea y administra tus horarios recurrentes."
        )

        top = QHBoxLayout()

        self.schedule_count_label = QLabel("0 horarios")
        self.schedule_count_label.setStyleSheet(
            "font-weight: 600; color: #555;"
        )

        new_button = QPushButton("+ Nuevo horario")
        new_button.setObjectName("Primary")
        new_button.clicked.connect(self.new_schedule)

        edit_button = QPushButton("Editar")
        edit_button.setObjectName("Secondary")
        edit_button.clicked.connect(self.edit_schedule)

        toggle_button = QPushButton("Activar / Desactivar")
        toggle_button.setObjectName("Secondary")
        toggle_button.clicked.connect(self.toggle_schedule)

        delete_button = QPushButton("Eliminar")
        delete_button.setObjectName("Danger")
        delete_button.clicked.connect(self.remove_schedule)

        top.addWidget(self.schedule_count_label)
        top.addStretch()
        top.addWidget(new_button)
        top.addWidget(edit_button)
        top.addWidget(toggle_button)
        top.addWidget(delete_button)

        layout.addLayout(top)

        self.schedule_list = QListWidget()
        layout.addWidget(self.schedule_list)

        return page

    def build_settings_page(self):
        page, layout = self.page_container(
            "Ajustes",
            "Configuración local del GUI."
        )

        card = QFrame()
        card.setObjectName("Card")
        form = QFormLayout(card)
        form.setContentsMargins(22, 22, 22, 22)
        form.setSpacing(14)

        self.user_id_edit = QLineEdit()
        self.user_id_edit.setPlaceholderText("Ejemplo: 123456789012345678")

        self.guild_id_edit = QLineEdit()
        self.guild_id_edit.setPlaceholderText("Ejemplo: 123456789012345678")

        timezone = QLineEdit(TIMEZONE_TEXT)
        timezone.setReadOnly(True)

        form.addRow("Discord User ID:", self.user_id_edit)
        form.addRow("Discord Server ID:", self.guild_id_edit)
        form.addRow("Zona horaria:", timezone)

        note = QLabel(
            "Los IDs se usan para asociar los horarios del GUI con tu "
            "cuenta y servidor. No compartas tu token de Discord aquí."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        form.addRow("", note)

        save_button = QPushButton("Guardar ajustes")
        save_button.setObjectName("Primary")
        save_button.clicked.connect(self.save_settings)

        form.addRow("", save_button)

        layout.addWidget(card)
        layout.addStretch()

        return page

    def show_page(self, index):
        self.stack.setCurrentIndex(index)

        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

        if index == 2:
            self.refresh_schedules()

    def load_settings(self):
        # Configuración inicial. En una siguiente versión la guardaremos
        # en un archivo de configuración separado.
        self.user_id_edit.setText("")
        self.guild_id_edit.setText("")

    def save_settings(self):
        user_id = self.user_id_edit.text().strip()
        guild_id = self.guild_id_edit.text().strip()

        if not user_id.isdigit() or not guild_id.isdigit():
            QMessageBox.warning(
                self,
                "Datos incorrectos",
                "User ID y Server ID deben ser números de Discord."
            )
            return

        self.user_id = user_id
        self.guild_id = guild_id

        self.update_home()
        self.refresh_schedules()

        QMessageBox.information(
            self,
            "Guardado",
            "Configuración guardada para esta sesión."
        )

    def require_ids(self):
        user_id = self.user_id_edit.text().strip()
        guild_id = self.guild_id_edit.text().strip()

        if not user_id.isdigit() or not guild_id.isdigit():
            QMessageBox.warning(
                self,
                "Configura el GUI primero",
                "Ve a ⚙️ Ajustes e introduce tu Discord User ID "
                "y Discord Server ID."
            )
            return None

        self.user_id = user_id
        self.guild_id = guild_id
        return user_id, guild_id

    def refresh_schedules(self):
        if not hasattr(self, "schedule_list"):
            return

        self.schedule_list.clear()

        if not self.user_id_edit.text().strip().isdigit():
            self.update_home()
            return

        user_id = int(self.user_id_edit.text().strip())

        try:
            rows = get_user_schedules(user_id)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Error de base de datos",
                f"No se pudieron cargar los horarios:\n{exc}"
            )
            return

        for row in rows:
            status = "🟢 Activo" if row["enabled"] else "⚪ Desactivado"
            confirmation = "Sí" if row["confirmation"] else "No"

            text = (
                f"{status}   {row['time']}   "
                f"{row['days']}   |   "
                f"{actions_text(row)}   |   "
                f"Confirmación: {confirmation}"
            )

            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.schedule_list.addItem(item)

        self.schedule_count_label.setText(f"{len(rows)} horarios")
        self.update_home(rows)

    def update_home(self, rows=None):
        if rows is None:
            if hasattr(self, "schedule_list"):
                count = self.schedule_list.count()
            else:
                count = 0
        else:
            count = len(rows)

        if hasattr(self, "home_schedules_card"):
            self.home_schedules_card._text_label.setText(
                f"{count} horarios configurados"
            )

        if hasattr(self, "home_config_card"):
            user = self.user_id_edit.text().strip()
            guild = self.guild_id_edit.text().strip()

            user_text = user if user else "pendiente"
            guild_text = guild if guild else "pendiente"

            self.home_config_card._text_label.setText(
                f"Servidor ID: {guild_text}\n"
                f"Usuario ID: {user_text}"
            )

    def selected_schedule_id(self):
        item = self.schedule_list.currentItem()

        if item is None:
            QMessageBox.information(
                self,
                "Selecciona un horario",
                "Selecciona primero un horario de la lista."
            )
            return None

        return int(item.data(Qt.ItemDataRole.UserRole))

    def get_selected_row(self):
        schedule_id = self.selected_schedule_id()
        if schedule_id is None:
            return None

        user_id = int(self.user_id_edit.text().strip())

        for row in get_user_schedules(user_id):
            if row["id"] == schedule_id:
                return row

        QMessageBox.warning(
            self,
            "Horario no encontrado",
            "El horario ya no existe o pertenece a otro usuario."
        )
        return None

    def new_schedule(self):
        ids = self.require_ids()
        if ids is None:
            return

        dialog = ScheduleDialog(self)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()
        user_id, guild_id = ids

        try:
            schedule_id = create_schedule(
                int(user_id),
                int(guild_id),
                values["time"],
                values["days"],
                values["mute"],
                values["deafen"],
                values["disconnect"],
                values["confirmation"],
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Error",
                f"No se pudo crear el horario:\n{exc}"
            )
            return

        self.refresh_schedules()

        QMessageBox.information(
            self,
            "Horario creado",
            f"Horario #{schedule_id} creado correctamente."
        )

    def edit_schedule(self):
        ids = self.require_ids()
        if ids is None:
            return

        row = self.get_selected_row()
        if row is None:
            return

        dialog = ScheduleDialog(self, row)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()

        try:
            updated = update_schedule(
                row["id"],
                int(ids[0]),
                values["time"],
                values["days"],
                values["mute"],
                values["deafen"],
                values["disconnect"],
                values["confirmation"],
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Error",
                f"No se pudo actualizar el horario:\n{exc}"
            )
            return

        self.refresh_schedules()

        if updated:
            QMessageBox.information(
                self,
                "Horario actualizado",
                "El horario fue actualizado correctamente."
            )
        else:
            QMessageBox.warning(
                self,
                "No actualizado",
                "No se pudo actualizar el horario."
            )

    def toggle_schedule(self):
        ids = self.require_ids()
        if ids is None:
            return

        row = self.get_selected_row()
        if row is None:
            return

        new_value = not bool(row["enabled"])

        try:
            updated = set_schedule_enabled(
                row["id"],
                int(ids[0]),
                new_value
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Error",
                f"No se pudo cambiar el estado:\n{exc}"
            )
            return

        self.refresh_schedules()

        if updated:
            status = "activado" if new_value else "desactivado"
            QMessageBox.information(
                self,
                "Estado cambiado",
                f"Horario {status}."
            )

    def remove_schedule(self):
        ids = self.require_ids()
        if ids is None:
            return

        row = self.get_selected_row()
        if row is None:
            return

        answer = QMessageBox.question(
            self,
            "Eliminar horario",
            f"¿Eliminar el horario de las {row['time']}?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            deleted = delete_schedule(
                row["id"],
                int(ids[0])
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Error",
                f"No se pudo eliminar el horario:\n{exc}"
            )
            return

        self.refresh_schedules()

        if deleted:
            QMessageBox.information(
                self,
                "Eliminado",
                "Horario eliminado correctamente."
            )


def main():
    init_database()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
