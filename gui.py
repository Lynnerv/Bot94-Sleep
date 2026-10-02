import sys
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
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
        self.setWindowTitle("Nuevo horario" if schedule is None else "Editar horario")
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setTime(datetime.now().time().replace(second=0, microsecond=0))
        form.addRow("Hora:", self.time_edit)

        layout.addLayout(form)

        days_label = QLabel("Días:")
        layout.addWidget(days_label)

        days_layout = QHBoxLayout()
        self.day_checks = {}

        for short, full in DAY_NAMES:
            check = QCheckBox(short)
            check.setToolTip(full.capitalize())
            check.setFixedWidth(38)
            self.day_checks[full] = check
            days_layout.addWidget(check)

        layout.addLayout(days_layout)

        actions_label = QLabel("Acciones:")
        layout.addWidget(actions_label)

        self.mute_check = QCheckBox("🔇 Mute")
        self.deafen_check = QCheckBox("🎧 Deafen")
        self.disconnect_check = QCheckBox("🚪 Disconnect")
        self.confirmation_check = QCheckBox("Pedir confirmación")

        self.deafen_check.toggled.connect(self.on_deafen_changed)

        layout.addWidget(self.mute_check)
        layout.addWidget(self.deafen_check)
        layout.addWidget(self.disconnect_check)
        layout.addWidget(self.confirmation_check)

        info = QLabel(
            "Nota: Deafen incluye automáticamente Mute, igual que el bot."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #777;")
        layout.addWidget(info)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if schedule is not None:
            self.load_schedule(schedule)

    def on_deafen_changed(self, checked):
        if checked:
            self.mute_check.setChecked(True)

    def load_schedule(self, row):
        hour, minute = map(int, row["time"].split(":"))
        from PySide6.QtCore import QTime
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
                "Selecciona al menos un día.",
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
                "Selecciona al menos una acción.",
            )
            return

        super().accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1050, 680)
        self.setMinimumSize(900, 600)

        self.user_id = ""
        self.guild_id = ""

        self.setStyleSheet("""
            QMainWindow {
                background: #f4f5f7;
            }

            QFrame#Sidebar {
                background: #202225;
            }

            QLabel#Logo {
                color: white;
                font-size: 22px;
                font-weight: 700;
                padding: 12px;
            }

            QPushButton#NavButton {
                color: #dcdcdc;
                background: transparent;
                border: none;
                text-align: left;
                padding: 13px 16px;
                font-size: 14px;
                border-radius: 6px;
            }

            QPushButton#NavButton:hover {
                background: #34373c;
            }

            QPushButton#NavButton:checked {
                background: #5865f2;
                color: white;
            }

            QLabel#Title {
                font-size: 27px;
                font-weight: 700;
                color: #202225;
            }

            QLabel#Subtitle {
                color: #666;
                font-size: 13px;
            }

            QFrame#Card {
                background: white;
                border: 1px solid #e1e3e6;
                border-radius: 10px;
            }

            QPushButton#Primary {
                background: #5865f2;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 16px;
                font-weight: 600;
            }

            QPushButton#Primary:hover {
                background: #4752c4;
            }

            QPushButton#Danger {
                background: #ed4245;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 9px 14px;
                font-weight: 600;
            }

            QLineEdit, QSpinBox, QTimeEdit, QComboBox {
                background: white;
                border: 1px solid #c9ccd1;
                border-radius: 6px;
                padding: 7px;
            }

            QListWidget {
                background: white;
                border: 1px solid #e1e3e6;
                border-radius: 8px;
            }

            QListWidget::item {
                padding: 12px;
                border-bottom: 1px solid #eee;
            }

            QListWidget::item:selected {
                background: #eef0ff;
                color: #202225;
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

        logo = QLabel("🌙 Bot94 Sleep")
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

        version = QLabel("Bot94 Sleep v0.2")
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
            "Panel principal de Bot94 Sleep."
        )

        grid = QGridLayout()
        grid.setSpacing(14)

        bot_card = self.make_card(
            "🤖 Estado del bot",
            "El GUI está listo.\n"
            "El bot de Discord debe seguir ejecutándose con bot.py."
        )

        server_card = self.make_card(
            "🖥️ Configuración",
            "Servidor: pendiente de configurar\n"
            "Usuario: pendiente de configurar"
        )
        self.home_config_card = server_card

        timezone_card = self.make_card(
            "🕐 Zona horaria",
            TIMEZONE_TEXT
        )

        schedules_card = self.make_card(
            "📅 Horarios",
            "0 horarios configurados"
        )
        self.home_schedules_card = schedules_card

        grid.addWidget(bot_card, 0, 0)
        grid.addWidget(server_card, 0, 1)
        grid.addWidget(timezone_card, 1, 0)
        grid.addWidget(schedules_card, 1, 1)

        layout.addLayout(grid)
        layout.addStretch()

        return page

    def make_card(self, title, text):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            "font-size: 16px; font-weight: 700; color: #202225;"
        )

        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setStyleSheet("color: #555; margin-top: 8px;")

        layout.addWidget(title_label)
        layout.addWidget(text_label)

        card._text_label = text_label
        return card

    def build_sleep_page(self):
        page, layout = self.page_container(
            "Sleep",
            "Temporizador para preparar una acción de sueño."
        )

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 22, 22, 22)

        label = QLabel("Dormir después de:")
        label.setStyleSheet("font-weight: 600;")

        self.sleep_minutes = QSpinBox()
        self.sleep_minutes.setRange(1, 1440)
        self.sleep_minutes.setValue(30)
        self.sleep_minutes.setSuffix(" minutos")

        self.sleep_mute = QCheckBox("🔇 Mute")
        self.sleep_deafen = QCheckBox("🎧 Deafen")
        self.sleep_disconnect = QCheckBox("🚪 Disconnect")
        self.sleep_confirmation = QCheckBox("Pedir confirmación")

        self.sleep_deafen.toggled.connect(
            lambda checked: self.sleep_mute.setChecked(True)
            if checked else None
        )

        card_layout.addWidget(label)
        card_layout.addWidget(self.sleep_minutes)
        card_layout.addSpacing(10)
        card_layout.addWidget(self.sleep_mute)
        card_layout.addWidget(self.sleep_deafen)
        card_layout.addWidget(self.sleep_disconnect)
        card_layout.addWidget(self.sleep_confirmation)

        info = QLabel(
            "La configuración del temporizador está preparada en el GUI. "
            "La ejecución directa desde el GUI se conectará al bot en el "
            "siguiente paso; por ahora /sleep en Discord sigue siendo la "
            "forma de ejecutar el temporizador."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #666;")
        card_layout.addWidget(info)

        button = QPushButton("Iniciar Sleep")
        button.setObjectName("Primary")
        button.clicked.connect(self.sleep_not_ready)
        card_layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(card)
        layout.addStretch()

        return page

    def sleep_not_ready(self):
        QMessageBox.information(
            self,
            "Sleep",
            "La configuración está lista, pero la ejecución del "
            "temporizador desde el GUI la conectaremos al bot en el "
            "siguiente paso.\n\n"
            "Mientras tanto puedes usar /sleep desde Discord."
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
        edit_button.clicked.connect(self.edit_schedule)

        toggle_button = QPushButton("Activar / Desactivar")
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
