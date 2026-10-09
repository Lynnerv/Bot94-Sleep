import sys
import requests
from PySide6.QtCore import QDate, QTime, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QDateEdit, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QTabWidget, QTimeEdit, QVBoxLayout, QWidget, QInputDialog)

API="http://127.0.0.1:8765"

class GUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("XZ94 · Control")
        self.resize(940,700)
        self.user_id=QLineEdit(); self.user_id.setPlaceholderText("ID numérico de Discord")
        self.guild_id=QLineEdit(); self.guild_id.setPlaceholderText("ID del servidor")
        self.api_status=QLabel("API: comprobando…")
        self.sleep_status=QLabel("⏱️ Sleep: inactivo")
        self.minutes=QSpinBox(); self.minutes.setRange(1,1440); self.minutes.setValue(30)
        self.schedule_date=QDateEdit(QDate.currentDate()); self.schedule_date.setCalendarPopup(True)
        self.schedule_date.setDisplayFormat("dd/MM/yyyy"); self.schedule_date.setMinimumDate(QDate.currentDate())
        self.schedule_time=QTimeEdit(QTime.currentTime()); self.schedule_time.setDisplayFormat("HH:mm")
        self.schedule_list=QListWidget()
        self.tabs=QTabWidget(); self.tabs.addTab(self.sleep_tab(),"🌙 Sleep"); self.tabs.addTab(self.schedule_tab(),"📅 Horarios")
        root=QWidget(); layout=QVBoxLayout(root)
        title=QLabel("XZ94  /  PANEL DE CONTROL"); title.setStyleSheet("font-size:22px;font-weight:700;padding:8px")
        layout.addWidget(title)
        form=QFormLayout(); form.addRow("User ID",self.user_id); form.addRow("Server ID",self.guild_id)
        layout.addLayout(form); layout.addWidget(self.api_status); layout.addWidget(self.tabs)
        self.setCentralWidget(root)
        self.setStyleSheet("""
        QMainWindow { background:#17191f; color:#e9eaf0; }
        QWidget { color:#e9eaf0; font-size:13px; }
        QLineEdit,QSpinBox,QDateEdit,QTimeEdit,QListWidget { background:#232630; border:1px solid #3c414e; border-radius:7px; padding:8px; }
        QPushButton { background:#343947; border:1px solid #484f60; border-radius:8px; padding:10px; }
        QPushButton:hover { background:#454d60; }
        QTabWidget::pane { border:1px solid #353a46; border-radius:8px; padding:8px; }
        QTabBar::tab { background:#252934; padding:10px 18px; margin:3px; border-radius:6px; }
        QTabBar::tab:selected { background:#77664a; }
        """)
        self.timer=QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(1000); self.refresh()

    def request(self,method,path,**kwargs):
        try:
            response=requests.request(method,API+path,timeout=2,**kwargs)
            try: data=response.json()
            except ValueError: data={"ok":False,"error":f"Respuesta inválida (HTTP {response.status_code})"}
            if not response.ok and data.get("ok",True): data={"ok":False,"error":f"HTTP {response.status_code}"}
            return data
        except requests.RequestException as exc:
            return {"ok":False,"error":str(exc)}

    def ids(self):
        u,g=self.user_id.text().strip(),self.guild_id.text().strip()
        if not u.isdigit() or not g.isdigit(): return None,None
        return int(u),int(g)

    def button(self,text,fn):
        b=QPushButton(text); b.clicked.connect(fn); return b

    def sleep_tab(self):
        w=QWidget(); l=QVBoxLayout(w)
        l.addWidget(QLabel("Duración del temporizador"))
        presets=QHBoxLayout()
        for label,value in [("15 min",15),("30 min",30),("45 min",45),("1 h",60),("1 h 15",75),("1 h 30",90)]:
            presets.addWidget(self.button(label,lambda checked=False,v=value:self.minutes.setValue(v)))
        l.addLayout(presets)
        form=QFormLayout(); form.addRow("Minutos personalizados",self.minutes); l.addLayout(form)
        actions=QHBoxLayout()
        actions.addWidget(self.button("➖ Reducir 5 min",lambda:self.adjust(-5)))
        actions.addWidget(self.button("✏️ Duración personalizada",self.custom_time))
        actions.addWidget(self.button("➕ Extender 5 min",lambda:self.adjust(5)))
        l.addLayout(actions)
        l.addWidget(self.button("🌙 Iniciar Sleep",self.start_sleep))
        l.addWidget(self.button("❌ Cancelar temporizador",self.cancel_sleep))
        self.sleep_status.setStyleSheet("font-size:16px;font-weight:600;padding:12px")
        l.addWidget(self.sleep_status); l.addStretch()
        return w

    def schedule_tab(self):
        w=QWidget(); l=QVBoxLayout(w)
        form=QFormLayout(); form.addRow("Fecha",self.schedule_date); form.addRow("Hora (24 h)",self.schedule_time)
        l.addLayout(form)
        l.addWidget(QLabel("Se ejecuta una sola vez. Zona horaria: America/Lima."))
        row=QHBoxLayout(); row.addWidget(self.button("💾 Guardar horario",self.create_schedule)); row.addWidget(self.button("🔄 Actualizar",self.load_schedules))
        l.addLayout(row); l.addWidget(self.schedule_list)
        controls=QHBoxLayout()
        controls.addWidget(self.button("Activar",lambda:self.set_schedule_enabled(True)))
        controls.addWidget(self.button("Desactivar",lambda:self.set_schedule_enabled(False)))
        controls.addWidget(self.button("Eliminar seleccionado",self.delete_schedule))
        l.addLayout(controls)
        return w

    def refresh(self):
        health=self.request("GET","/api/health")
        if health.get("ok"):
            self.api_status.setText("🟢 API conectada · Bot listo" if health.get("bot_ready") else "🟡 API conectada · esperando al bot")
        else: self.api_status.setText("🔴 API desconectada · inicia apibot.py")
        u,g=self.ids()
        if u and g:
            s=self.request("GET","/api/sleep/status",params={"user_id":u,"guild_id":g})
            if s.get("ok"):
                if s.get("active"):
                    sec=s.get("remaining_seconds",0)
                    self.sleep_status.setText(f"🟢 Sleep activo · {sec//60:02d}:{sec%60:02d} restantes")
                else:
                    status=s.get("status","idle")
                    label={"idle":"Inactivo","completed":"Completado","cancelled":"Cancelado","failed":"Error"}.get(status,status)
                    self.sleep_status.setText(f"⏱️ Sleep: {label}")
        self.setWindowTitle("XZ94 · Control")

    def custom_time(self):
        value,ok=QInputDialog.getInt(self,"Sleep personalizado","Minutos:",self.minutes.value(),1,1440)
        if ok:self.minutes.setValue(value)

    def require_ids(self):
        u,g=self.ids()
        if not u or not g:
            QMessageBox.warning(self,"IDs","Introduce IDs numéricos de usuario y servidor.")
            return None,None
        return u,g

    def start_sleep(self):
        u,g=self.require_ids()
        if not u:return
        d=self.request("POST","/api/sleep/start",json={"user_id":u,"guild_id":g,"minutes":self.minutes.value()})
        if not d.get("ok"):QMessageBox.warning(self,"Sleep",d.get("error","No se pudo iniciar."))

    def adjust(self,delta):
        u,g=self.require_ids()
        if not u:return
        d=self.request("POST","/api/sleep/adjust",json={"user_id":u,"guild_id":g,"delta":delta})
        if not d.get("ok"):QMessageBox.warning(self,"Sleep",d.get("error","No se pudo ajustar."))

    def cancel_sleep(self):
        u,g=self.require_ids()
        if not u:return
        d=self.request("POST","/api/sleep/cancel",json={"user_id":u,"guild_id":g})
        if not d.get("ok"):QMessageBox.warning(self,"Sleep",d.get("error","No se pudo cancelar."))

    def create_schedule(self):
        u,g=self.require_ids()
        if not u:return
        date=self.schedule_date.date().toString("yyyy-MM-dd")
        time=self.schedule_time.time().toString("HH:mm")
        d=self.request("POST","/api/schedules",json={"user_id":u,"guild_id":g,"date":date,"time":time})
        if not d.get("ok"):QMessageBox.warning(self,"Horario",d.get("error","No se pudo guardar."))
        else:self.load_schedules()

    def load_schedules(self):
        u,g=self.ids(); self.schedule_list.clear()
        if not u:return
        d=self.request("GET","/api/schedules",params={"user_id":u,"guild_id":g})
        if not d.get("ok"):
            QMessageBox.warning(self,"Horarios",d.get("error","No se pudieron cargar.")); return
        for item in d.get("schedules",[]):
            status="Activo" if item["enabled"] else "Inactivo"
            self.schedule_list.addItem(f"#{item['id']} | {item['date']} | {item['time']} | {status}")

    def selected_schedule_id(self):
        item=self.schedule_list.currentItem()
        if not item:return None
        try:return int(item.text().split("|",1)[0].strip().lstrip("#"))
        except ValueError:return None

    def set_schedule_enabled(self,enabled):
        u,g=self.require_ids(); sid=self.selected_schedule_id()
        if not u:return
        if not sid:QMessageBox.information(self,"Horarios","Selecciona un horario.");return
        d=self.request("PATCH","/api/schedules",json={"user_id":u,"schedule_id":sid,"enabled":enabled})
        if not d.get("ok") or not d.get("ok",False):QMessageBox.warning(self,"Horario",d.get("error","No se pudo actualizar."))
        self.load_schedules()

    def delete_schedule(self):
        u,g=self.require_ids(); sid=self.selected_schedule_id()
        if not u:return
        if not sid:QMessageBox.information(self,"Horarios","Selecciona un horario.");return
        confirm=QMessageBox.question(self,"Eliminar","¿Eliminar el horario seleccionado?")
        if confirm!=QMessageBox.Yes:return
        d=self.request("DELETE","/api/schedules",json={"user_id":u,"schedule_id":sid})
        if not d.get("ok"):QMessageBox.warning(self,"Horario",d.get("error","No se pudo eliminar."))
        self.load_schedules()

if __name__=="__main__":
    app=QApplication(sys.argv); window=GUI(); window.show(); sys.exit(app.exec())
