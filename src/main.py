# src/main.py
import tkinter as tk
from tkinter import messagebox
import threading
import subprocess
import sys
import shutil
from types import SimpleNamespace
from PIL import Image, ImageTk
import pyautogui
import screeninfo
import cv2
import time
import os

# --- Importações das Views ---
from ui.dashboard_view import DashboardFrame
from ui.calibrator_view import CalibratorFrame
from ui.notepad_view import NotepadFrame

from tracking.eye_tracker import EyeTracker
from tracking import calibration

# --- CONSTANTES ---
SNAP_THRESHOLD_PIXELS = 300
CAM_PROBE_MAX = 4
PREVIEW_SIZE = (320, 240)  # Usado pela tela de startup
GAZE_MOVE_DELAY = 5
GAZE_STABILITY_DELAY = 1.5
GAZE_TOLERANCE_PX = 80
SCAN_DELAY_SECONDS = 3.0  # Tempo de varredura (3 segundos)


# --- CLASSE PRINCIPAL (CONTROLLER) ---
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        pyautogui.FAILSAFE = False

        # --- Estado do Gaze ---
        self.last_gaze_move_time = 0
        self.last_gaze_pos = None
        self.last_stable_time = time.time()
        self.last_cursor_pos = None
        self.just_clicked_time = 0

        self.title("Assistente de Acessibilidade Ocular")
        self.configure(bg="#222")
        self.minsize(560, 520)

        # --- Estado Geral do App ---
        self.tracker = None
        self.shared_state = {"_lock": threading.RLock()}
        self.mouse_control_enabled = False
        self.focusable_widgets = []
        self.currently_snapped_widget = None
        self.selected_monitor_index = 0
        self.default_camera_index = 0

        # --- Estado de Navegação e UI ---
        self.current_screen = None  # A instância da View ativa
        self._update_loop_job = None
        self._modal_widget_backup = []
        self.is_navigating = False

        # --- Estado do Modo de Varredura (Scanner) ---
        self.scan_mode_active = False
        self.keyboard_frame_widget = None # Referência ao frame do teclado
        self.scan_key_list = []           # Lista de teclas em ordem
        self.scan_index = -1              # Índice da tecla selecionada
        self.last_scan_time = 0           # Timer para o scanner
        self.is_dwell_clicking = False    # Pausa o scanner
        self.dwell_start_time = 0         # Timer da piscada de 2s
        self.blink_pre_dwell_start_time = 0 # <-- NOVO: Timer da piscada de 1s

        # --- Estado de Clique (Substitui click_request) ---
        self.blink_state = "IDLE" # IDLE, PRE_LOCKED, LOCKED
        self.blink_start_time = 0
        self.BLINK_CLICK_DURATION_DASHBOARD = 1.0 # O 1seg antigo
        self.BLINK_CLICK_DURATION_SCANNER = 0.3   
        self.SCAN_DWELL_PRE_TIMER_SECONDS = 0.7   

        # --- CONSTANTES DE COR DO SCANNER ---
        self.KEY_STYLE_BG = "#EEEEEE"
        self.SPECIAL_KEY_STYLE_BG = "#CCCCCC"
        self.HIGHLIGHT_BG = "#00ff00" # Verde-limão
        self.HIGHLIGHT_THICKNESS = 6  # Espessura do destaque

        # --- Vars do Tkinter (para as views lerem/escreverem) ---
        self.profile_var = None
        self.monitor_var = None
        self.camera_var = None

        # Monitores e Câmeras
        self.available_monitors = self._get_monitores_com_fallback()
        print("Monitores detectados:")
        for i, m in enumerate(self.available_monitors):
            print(f"  {i}: {m.width}x{m.height} @ ({m.x},{m.y})")
        self._camera_list = self._probe_cameras(CAM_PROBE_MAX)

        # --- Estado do Preview da Tela Inicial ---
        self._preview_cap = None
        self._preview_job = None

        # --- Estado do Bloco de Notas (Controlado aqui) ---
        self.notepad_text_widget = None
        self.sticky_shift_active = False
        self.caps_lock_active = False
        self.notepad_is_dirty = False
        self.notepad_last_save_content = ""
        self.notepad_save_dir = os.path.join(os.path.expanduser("~"), "Documentos", "SimpleEyeTracker")
        self.shift_btn_ref = None  # Referências para botões do teclado
        self.caps_btn_ref = None

        # --- Recursos (Ícones) ---
        self.icon_home = None
        self.icon_notepad = None
        self._load_sidebar_icons()

        # Tela inicial
        self._build_startup_frame()

    # -------- Utilidades de Hardware/OS ----------

    def _get_monitores_com_fallback(self):
        try:
            mons = screeninfo.get_monitors()
            if mons:
                return mons
        except Exception:
            pass
        size = pyautogui.size()
        return [SimpleNamespace(width=size.width, height=size.height, x=0, y=0)]

    def get_active_monitor(self):
        idx = getattr(self, "selected_monitor_index", 0)
        if 0 <= idx < len(self.available_monitors):
            return self.available_monitors[idx]
        return self.available_monitors[0]

    def move_root_to_monitor(self, idx=None, fullscreen_like=True):
        if idx is not None:
            self.selected_monitor_index = idx
        mon = self.get_active_monitor()
        self.geometry(f"{mon.width}x{mon.height}+{mon.x}+{mon.y}")
        if fullscreen_like:
            try:
                self.overrideredirect(False)
                self.attributes("-fullscreen", False)
            except Exception:
                pass

    def _probe_cameras(self, max_test=4):
        cams = []
        for i in range(max_test):
            cap = cv2.VideoCapture(i)
            if not cap or not cap.isOpened():
                if cap:
                    cap.release()
                continue
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            label = f"Câmera {i}"
            if width and height:
                label += f" ({width}x{height})"
            cams.append({"index": i, "label": label})
            cap.release()
        if not cams:
            cams = [{"index": 0, "label": "Câmera 0"}]
        return cams

    def _load_sidebar_icons(self):
        """Carrega e redimensiona ícones para as views usarem."""
        ICON_SIZE = (48, 48)
        ICON_HOME_PATH = "resources/images/home.png"
        ICON_NOTEPAD_PATH = "resources/images/notepad.png"

        try:
            img = Image.open(ICON_HOME_PATH).resize(ICON_SIZE, Image.LANCZOS)
            self.icon_home = ImageTk.PhotoImage(img)
        except Exception as e:
            print(f"Erro ao carregar ícone 'home': {e}")

        try:
            img = Image.open(ICON_NOTEPAD_PATH).resize(ICON_SIZE, Image.LANCZOS)
            self.icon_notepad = ImageTk.PhotoImage(img)
        except Exception as e:
            print(f"Erro ao carregar ícone 'notepad' para sidebar: {e}")

    # --------- UI: Tela inicial (Startup) ----------
    # (Esta é a única UI construída diretamente no main)

    def _build_startup_frame(self):
        """Tela inicial simples dentro do root para escolher monitor e câmera."""
        self._clear_root()
        frame = tk.Frame(self, bg="#222")
        frame.pack(expand=True, fill="both", padx=30, pady=30)

        tk.Label(frame, text="Selecione o Monitor e a Câmera",
                 font=("Arial", 18, "bold"), bg="#222", fg="white").pack(pady=(0, 20))

        # Monitor
        tk.Label(frame, text="Monitor:", font=("Arial", 14), bg="#222", fg="white").pack()
        monitor_options = [f"Monitor {i} ({m.width}x{m.height})" for i, m in enumerate(self.available_monitors)]
        self.start_monitor_var = tk.StringVar(value=monitor_options[0])
        tk.OptionMenu(frame, self.start_monitor_var, *monitor_options).pack(pady=(5, 15))

        # Câmeras
        cam_labels = [c["label"] for c in self._camera_list] or ["Câmera 0"]
        tk.Label(frame, text="Câmera:", font=("Arial", 14), bg="#222", fg="white").pack()
        self.start_camera_var = tk.StringVar(value=cam_labels[0])
        cam_menu = tk.OptionMenu(frame, self.start_camera_var, *cam_labels, command=self._on_start_cam_change)
        cam_menu.pack(pady=(5, 10))

        # Preview
        self.preview_holder = tk.Label(frame, bg="#000")
        self.preview_holder.pack(pady=(6, 16))
        if cam_labels:
            self._on_start_cam_change(self.start_camera_var.get())

        # Botões
        btns = tk.Frame(frame, bg="#222")
        btns.pack(pady=10)
        tk.Button(btns, text="Iniciar", font=("Arial", 14, "bold"),
                  command=self._confirmar_startup).pack(side="left", padx=10)
        tk.Button(btns, text="Sair", font=("Arial", 14),
                  command=self.quit_app).pack(side="left", padx=10)

        # Centraliza
        mon0 = self.available_monitors[0]
        w, h = 600, 560
        x = mon0.x + (mon0.width - w) // 2
        y = mon0.y + (mon0.height - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _on_start_cam_change(self, selected_label):
        if not self._camera_list:
            return
        cam = next((c for c in self._camera_list if c["label"] == selected_label), self._camera_list[0])
        self._start_camera_preview(cam["index"])

    def _start_camera_preview(self, cam_index: int):
        self._stop_camera_preview()
        self._preview_cap = cv2.VideoCapture(cam_index)
        self._preview_loop()

    def _preview_loop(self):
        if not self._preview_cap:
            return
        ret, frame = self._preview_cap.read()
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame).resize(PREVIEW_SIZE)
            imgtk = ImageTk.PhotoImage(img)
            self.preview_holder.configure(image=imgtk, text="")
            self.preview_holder.image = imgtk
        self._preview_job = self.after(33, self._preview_loop)

    def _stop_camera_preview(self):
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
            self._preview_job = None
        if self._preview_cap is not None:
            try:
                self._preview_cap.release()
            except Exception:
                pass
            self._preview_cap = None

    def _confirmar_startup(self):
        # Parar preview da tela inicial
        self._stop_camera_preview()

        try:
            self.selected_monitor_index = int(self.start_monitor_var.get().split()[1])
        except Exception:
            self.selected_monitor_index = 0

        if self._camera_list:
            sel_label = self.start_camera_var.get()
            cam = next((c for c in self._camera_list if c["label"] == sel_label), self._camera_list[0])
            self.default_camera_index = cam["index"]
        else:
            self.default_camera_index = 0

        self.move_root_to_monitor(self.selected_monitor_index)
        self.create_calibrator_view()  # Navega para a tela de calibração

    # --------- Lógica de Navegação e UI ----------

    def abrir_configuracoes_view(self):
        """Abre a Toplevel de Configurações."""
        config = tk.Toplevel(self)
        config.title("Configurações")
        config.configure(bg="#222")
        config.resizable(False, False)
        tk.Label(config, text="Configurações", font=("Arial", 16, "bold"), bg="#222", fg="white").pack(pady=20)
        tk.Button(config, text="Trocar ou Gerenciar Perfis", command=self.create_calibrator_view).pack(pady=10)
        tk.Button(config, text="Fechar", command=config.destroy).pack(pady=20)

        mon = self.get_active_monitor()
        config.geometry(f"400x300+{mon.x + 100}+{mon.y + 100}")
        config.transient(self)

    def create_notepad_view(self):
        """Navega para a View do Bloco de Notas."""
        self._clear_root()
        self.title("Bloco de Notas - Controle Ocular")
        self.configure(bg="#0b4073")

        notepad_view = NotepadFrame(self, controller=self)
        notepad_view.pack(fill="both", expand=True)

        self.current_screen = notepad_view
        self.focusable_widgets = notepad_view.get_focusable_widgets()

        # Pega as referências da View para o modo de varredura
        self.keyboard_frame_widget = notepad_view.keyboard_frame
        self.scan_key_list = notepad_view.get_scan_keys()

        self.bind("<F7>", self.toggle_mouse_control)
        self.bind("<Escape>", self._handle_scan_exit) # LIGA O ESCAPE
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.move_root_to_monitor()

        self._update_status_label()  # Atualiza o status
        self.update_loop()  # Inicia o loop

    def create_calibrator_view(self):
        """Navega para a View de Calibração."""
        self._clear_root()
        self.configure(bg="#222")

        calib_view = CalibratorFrame(self, controller=self)
        calib_view.pack(expand=True)

        self.current_screen = calib_view
        # Esta tela não tem widgets focáveis pelo gaze
        self.focusable_widgets = []

        self.move_root_to_monitor(fullscreen_like=False)  # Não maximiza
        # Centraliza a janela de calibração
        mon = self.get_active_monitor()
        w, h = 800, 850
        x = mon.x + (mon.width - w) // 2
        y = mon.y + (mon.height - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # Esta tela não tem loop de gaze nem status F7
        # Ela apenas espera o usuário clicar em "Carregar" ou "Calibrar"

    def create_dashboard(self):
        """Navega para a View do Dashboard."""
        self._clear_root()
        self.title("Dashboard - Controle Ocular")
        self.configure(bg="#0b4073")

        dashboard_view = DashboardFrame(self, controller=self)
        dashboard_view.pack(fill="both", expand=True)

        self.current_screen = dashboard_view
        self.focusable_widgets = dashboard_view.get_focusable_widgets()

        self.bind("<F7>", self.toggle_mouse_control)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.move_root_to_monitor(self.selected_monitor_index)

        self._update_status_label()
        self.update_loop()

    # --------- Lógica de Calibração (Controller) ----------

    def get_profile_list(self):
        """Helper para a View de Calibração pegar os perfis."""
        return calibration.list_profiles()

    def load_profile_and_start(self):
        """Lógica para carregar um perfil."""
        profile_name = self.profile_var.get()
        calib_data = calibration.load_profile(profile_name)
        if not calib_data:
            messagebox.showerror("Erro", f"Não foi possível carregar o perfil '{profile_name}'.")
            return

        self.selected_monitor_index = int(calib_data.get("monitor_index", self.selected_monitor_index))
        camera_index = int(calib_data.get("camera_index", self.default_camera_index))
        self.move_root_to_monitor(self.selected_monitor_index)

        self._clear_root()  # Limpa a tela de calibração

        self.tracker = EyeTracker(camera_index=camera_index, shared_state=self.shared_state)
        self.tracker.load_calibration(calib_data, profile_name)
        self.tracker.start()
        self.create_dashboard()  # Navega para o Dashboard

    def run_calibration(self):
        """Lógica para iniciar um novo processo de calibração."""
        try:
            self.selected_monitor_index = int(self.monitor_var.get().split()[1])
        except Exception:
            pass

        cam_label = self.camera_var.get()
        cam = next((c for c in self._camera_list if c["label"] == cam_label), None)
        camera_index = cam["index"] if cam else self.default_camera_index

        self.move_root_to_monitor(self.selected_monitor_index)

        profile_name = self._ask_profile_name(self.get_active_monitor())
        if not profile_name:
            return

        messagebox.showinfo(
            "Instruções",
            "Pressione 'C' para travar, 'S' para calibrar o centro, e 'Q' para sair."
        )

        self._clear_root()  # Limpa a tela de calibração

        self.tracker = EyeTracker(camera_index=camera_index, shared_state=self.shared_state)
        mon = self.get_active_monitor()
        self.tracker.start_debug_window(window_pos=(mon.x + 80, mon.y + 80))

        calib_data = self.tracker.save_calibration()
        if calib_data:
            calib_data["monitor_index"] = self.selected_monitor_index
            calib_data["camera_index"] = camera_index
            calibration.save_profile(profile_name, calib_data)

        self.tracker.start()
        self.tracker.loaded_profile_name = profile_name
        self.create_dashboard()  # Navega para o Dashboard

    def _ask_profile_name(self, monitor):
        """Diálogo customizado para nome do perfil."""
        dialog = tk.Toplevel(self)
        dialog.title("Novo Perfil")
        dialog.configure(bg="#222")

        w, h = 400, 200
        x = monitor.x + (monitor.width // 2 - w // 2)
        y = monitor.y + (monitor.height // 2 - h // 2)
        dialog.geometry(f"{w}x{h}+{x}+{y}")
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(dialog, text="Digite um nome para o novo perfil:",
                 bg="#222", fg="white", font=("Arial", 14)).pack(pady=20)
        entry = tk.Entry(dialog, font=("Arial", 14))
        entry.pack(pady=10)
        entry.focus_set()

        result = {"value": None}

        def confirmar():
            result["value"] = entry.get().strip()
            dialog.destroy()

        tk.Button(dialog, text="OK", command=confirmar).pack(pady=20)

        self.wait_window(dialog)
        return result["value"]

    # --------- Lógica do Bloco de Notas (Controller) ----------

    def _on_notepad_modified(self, event=None):
        """Chamado quando o texto é alterado. Seta o flag 'dirty'."""
        self.notepad_is_dirty = True
        if self.notepad_text_widget:
            self.notepad_text_widget.edit_modified(False)

    def _handle_save_document(self):
        """Salva o conteúdo atual em um .txt com timestamp."""
        if not self.notepad_text_widget:
            return
        try:
            os.makedirs(self.notepad_save_dir, exist_ok=True)
            content = self.notepad_text_widget.get("1.0", tk.END)

            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(self.notepad_save_dir, f"bloco_de_notas_{timestamp}.txt")

            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)

            self.notepad_is_dirty = False
            self.notepad_last_save_content = content
            self._show_custom_modal("Salvo", f"Documento salvo com sucesso em:\n{filename}")
        except Exception as e:
            messagebox.showerror("Erro ao Salvar", f"Não foi possível salvar o arquivo:\n{e}")

    def _handle_new_document(self):
        """Limpa o editor ou pede para salvar se houver alterações."""
        if not self.notepad_text_widget:
            return
        current_content = self.notepad_text_widget.get("1.0", tk.END)
        is_empty = not current_content.strip()

        if is_empty or not self.notepad_is_dirty:
            self._clear_notepad()
        else:
            # TODO: Substituir por modal customizado de Sim/Não/Cancelar
            resposta = messagebox.askyesnocancel("Novo Documento", "Deseja salvar as alterações?")

            if resposta is True:
                self._handle_save_document()
                self._clear_notepad()
            elif resposta is False:
                self._clear_notepad()
            elif resposta is None:
                pass

    def _clear_notepad(self):
        """Limpa o widget de texto e reseta os flags."""
        self.notepad_last_save_content = ""
        self.notepad_is_dirty = False
        if self.notepad_text_widget:
            self.notepad_text_widget.delete("1.0", tk.END)
            self.notepad_text_widget.edit_modified(False)

    def _show_custom_modal(self, title, message):
        """Cria um Toplevel modal (pop-up) controlável pelo olhar."""

        self._modal_widget_backup = self.focusable_widgets
        self.focusable_widgets = []
        self.currently_snapped_widget = None

        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg="#222")
        dialog.resizable(False, False)

        mon = self.get_active_monitor()
        w, h = 450, 220
        x = mon.x + (mon.width // 2 - w // 2)
        y = mon.y + (mon.height // 2 - h // 2)
        dialog.geometry(f"{w}x{h}+{x}+{y}")

        dialog.transient(self)
        dialog.grab_set()

        msg_label = tk.Label(dialog, text=message, bg="#222", fg="white",
                             font=("Arial", 14), wraplength=400, justify="center")
        msg_label.pack(pady=30, padx=20, expand=True)

        def _close_dialog():
            self.focusable_widgets = self._modal_widget_backup
            self._modal_widget_backup = []
            self.currently_snapped_widget = None
            dialog.grab_release()
            dialog.destroy()

        ok_button = tk.Button(dialog, text="OK", font=("Arial", 16, "bold"),
                              command=_close_dialog, width=10)
        ok_button.pack(pady=20)

        self.focusable_widgets.append(ok_button)
        dialog.protocol("WM_DELETE_WINDOW", _close_dialog)

    # --------- Lógica de Gaze e Loop (Controller) ----------

    def _get_current_gaze_coords(self):
        """Helper para obter as coordenadas (x, y) atuais do olhar."""
        if not self.tracker:
            return None
            
        gaze_data = self.tracker.get_screen_gaze()
        if not gaze_data:
            return None

        gaze_x, gaze_y, _, _, _ = gaze_data
        mon = self.get_active_monitor()
        final_gaze_x = gaze_x + mon.x
        final_gaze_y = gaze_y + mon.y
        return (final_gaze_x, final_gaze_y)

    def _get_gaze_to_widget_dist(self, widget):
        """Helper que retorna a distância do olhar atual ao centro de um widget."""
        if not widget or not widget.winfo_exists():
            return None
        
        gaze_coords = self._get_current_gaze_coords()
        if not gaze_coords:
            return None
            
        gx, gy = gaze_coords
        
        x = widget.winfo_rootx()
        y = widget.winfo_rooty()
        w = widget.winfo_width()
        h = widget.winfo_height()
        center_x, center_y = x + w / 2, y + h / 2
        
        dist_sq = (final_gaze_x - center_x) ** 2 + (final_gaze_y - center_y) ** 2
        return dist_sq ** 0.5

    def _is_gaze_in_widget_bounds(self, widget):
        """Verifica se as coordenadas atuais do olhar estão DENTRO dos limites de um widget."""
        if not widget or not widget.winfo_exists():
            return False
            
        gaze_coords = self._get_current_gaze_coords()
        if not gaze_coords:
            return False # Não há dados do olhar
            
        gx, gy = gaze_coords
        
        x1 = widget.winfo_rootx()
        y1 = widget.winfo_rooty()
        x2 = x1 + widget.winfo_width()
        y2 = y1 + widget.winfo_height()
        
        return (x1 <= gx <= x2) and (y1 <= gy <= y2)
    
    def _handle_scan_exit(self, event=None):
        """Lida com a tecla 'Escape' para sair do modo de varredura."""
        if not self.scan_mode_active:
            return

        print("[Scanner] Saída forçada via 'Escape'. Desativando.")
        self.scan_mode_active = False

        # Limpa o destaque da última tecla
        if 0 <= self.scan_index < len(self.scan_key_list):
            try:
                key = self.scan_key_list[self.scan_index]
                is_special = len(key.cget('text')) > 1 or not key.cget('text').isalnum()
                bg_to_set = self.SPECIAL_KEY_STYLE_BG if is_special else self.KEY_STYLE_BG
                key.configure(
                    highlightbackground=bg_to_set, 
                    highlightthickness=4
                )
            except: pass 
                
        self.scan_index = -1
        self.is_dwell_clicking = False
        self.blink_pre_dwell_start_time = 0

    def _handle_scan_mode(self):
        """Lógica principal do modo de varredura (Fases 2 e 3)."""
        
        now = time.time()
        
        # --- LÓGICA FASE 3: CHECA PISCADA ---
        is_blinking = False
        lock = self.shared_state.get("_lock")
        if lock:
            with lock:
                is_blinking = self.shared_state.get("is_blinking", False)
        else:
            return 

        if is_blinking:
            # --- Inicia o pré-timer (1s) ---
            if self.blink_pre_dwell_start_time == 0:
                self.blink_pre_dwell_start_time = time.time()

            # --- Checa se o Dwell (pausa) deve ser ativado ---
            if not self.is_dwell_clicking and (time.time() - self.blink_pre_dwell_start_time) >= self.SCAN_DWELL_PRE_TIMER_SECONDS:
                self.is_dwell_clicking = True
                self.dwell_start_time = time.time()
                print("[Scanner] Dwell (1s) detectado. Pausando scan.")
            
            # --- Se o Dwell ESTÁ ativo, checa o clique (2s) ---
            if self.is_dwell_clicking:
                if (time.time() - self.dwell_start_time) >= self.BLINK_CLICK_DURATION_SCANNER:
                    print("[Scanner] CLIQUE (2s)!")
                    if 0 <= self.scan_index < len(self.scan_key_list):
                        try:
                            key = self.scan_key_list[self.scan_index]
                            key.invoke()
                        except: pass
                    
                    self.is_dwell_clicking = False
                    self.dwell_start_time = 0
                    self.blink_pre_dwell_start_time = 0
                    self.last_scan_time = time.time()
                    self.just_clicked_time = time.time()
            
            # Se está piscando (em pré-timer ou dwell), não avança o scanner.
            return 

        else: # Não está piscando (olhos abertos)
            if self.is_dwell_clicking:
                print("[Scanner] Clique cancelado (abriu os olhos).")
                self.is_dwell_clicking = False
                self.dwell_start_time = 0
                self.last_scan_time = time.time() # Resume o scanner
            
            # Reseta o pré-timer se a piscada foi involuntária
            if self.blink_pre_dwell_start_time > 0:
                self.blink_pre_dwell_start_time = 0
        # --- FIM DA LÓGICA FASE 3 ---

        # --- Lógica do Scanner (Fase 2) ---
        # SÓ avança o scanner se NÃO estivermos no meio de um clique
        if (now - self.last_scan_time) >= SCAN_DELAY_SECONDS:
            
            # 3a. Remove o destaque da tecla anterior
            if 0 <= self.scan_index < len(self.scan_key_list):
                try:
                    key = self.scan_key_list[self.scan_index]
                    is_special = len(key.cget('text')) > 1 or not key.cget('text').isalnum()
                    bg_to_set = self.SPECIAL_KEY_STYLE_BG if is_special else self.KEY_STYLE_BG
                    
                    if key == self.shift_btn_ref and self.sticky_shift_active:
                         bg_to_set = "#1E88E5"
                    elif key == self.caps_btn_ref and self.caps_lock_active:
                         bg_to_set = "#1E88E5"
                    
                    key.configure(
                        highlightbackground=bg_to_set, 
                        highlightthickness=4
                    )
                except: pass 
            
            # 3b. Avança o índice
            self.scan_index += 1
            
            # 3c. Loopa de volta ao início
            if self.scan_index >= len(self.scan_key_list):
                self.scan_index = 0
                
            # 3d. Adiciona destaque à nova tecla
            if 0 <= self.scan_index < len(self.scan_key_list):
                try:
                    new_key = self.scan_key_list[self.scan_index]
                    new_key.configure(
                        highlightbackground=self.HIGHLIGHT_BG, 
                        highlightthickness=self.HIGHLIGHT_THICKNESS
                    )
                except: pass
            
            # 3e. Reseta o timer
            self.last_scan_time = time.time()


    def _update_status_label(self):
        """Helper para atualizar o label de status na view ativa."""
        state_text = "ATIVADO" if self.mouse_control_enabled else "DESATIVADO"
        full_text = f"Eye Tracking: {state_text} (Pressione F7)"
        try:
            if self.current_screen and hasattr(self.current_screen, "update_status_label"):
                self.current_screen.update_status_label(full_text)
        except tk.TclError:
            pass  # Ignora erro se a janela foi destruída

    def toggle_mouse_control(self, event=None):
        self.mouse_control_enabled = not self.mouse_control_enabled
        self._update_status_label()
        if not self.mouse_control_enabled:
            self.currently_snapped_widget = None

    def update_loop(self):
        if self.mouse_control_enabled and self.tracker:

            # Verifica o Modo de Varredura PRIMEIRO
            if self.scan_mode_active:
                self._handle_scan_mode()
                self._update_loop_job = self.after(50, self.update_loop)
                return
            
            # 1. Checa "congelamento" pós-clique
            now = time.time()
            if self.just_clicked_time and (now - self.just_clicked_time < GAZE_MOVE_DELAY):
                self._update_loop_job = self.after(50, self.update_loop)
                return
            self.just_clicked_time = 0

            lock = self.shared_state.get("_lock")
            if lock is None:
                self._update_loop_job = self.after(50, self.update_loop)
                return
                
            self.is_navigating = False # Reseta o flag

            # 2. Pega clique (Lógica do Dashboard)
            click_request = False # Flag de clique para este ciclo
            is_blinking = False
            with lock:
                is_blinking = self.shared_state.get("is_blinking", False)

            if self.blink_state == "IDLE":
                if is_blinking:
                    self.blink_state = "PRE_LOCKED"
                    self.blink_start_time = time.time()
            elif self.blink_state == "PRE_LOCKED":
                if not is_blinking: # Abriu os olhos
                    self.blink_state = "IDLE"
                elif (time.time() - self.blink_start_time) > self.BLINK_CLICK_DURATION_DASHBOARD:
                    click_request = True
                    self.blink_state = "LOCKED" # Espera abrir os olhos
            elif self.blink_state == "LOCKED":
                if not is_blinking: # Abriu os olhos
                    self.blink_state = "IDLE"
            # --- FIM DA LÓGICA DE CLIQUE ---

            # 3. Processa clique (Modo Snap)
            if click_request and self.currently_snapped_widget:
                widget = self.currently_snapped_widget
                
                widget_info = ""
                if isinstance(widget, (tk.Button, tk.Label)):
                    try: widget_info = widget.cget('text')
                    except Exception: widget_info = str(widget)
                elif isinstance(widget, tk.Text):
                    widget_info = "Área de Texto"
                else:
                    widget_info = str(widget)
                print(f"[EyeTracker] Clique ocular em: {widget_info}")

                # Feedback visual
                try:
                    original_color = widget.cget("highlightbackground")
                    widget.configure(highlightbackground="#00ff88", highlightthickness=8)
                    widget.after(150,
                                 lambda: widget.configure(highlightbackground=original_color, highlightthickness=6))
                except: pass

                # (Não precisamos mais do lock para 'click_request')

                # Executa ação
                try:
                    widget.invoke()
                except tk.TclError:
                    # Se não for botão (ex: tk.Text), apenas foca
                    widget.focus_set()
                except Exception as e:
                    print(f"[EyeTracker] Erro no clique ocular (invoke): {e}")
                
                if self.is_navigating:
                    return
                
                pyautogui.click()
                self.just_clicked_time = time.time()
                
                self._update_loop_job = self.after(50, self.update_loop)
                return

            # 4. Limpa clique (se não usado)
            # (Não é mais necessário, click_request é local)

            # 5. Processa movimento do olhar
            with lock:
                # O 'gaze_frozen' não existe mais
                gaze_data = self.shared_state.get("gaze")
            
            if gaze_data:
                gaze_x, gaze_y, _, _, _ = gaze_data
                mon = self.get_active_monitor()
                final_gaze_x = gaze_x + mon.x
                final_gaze_y = gaze_y + mon.y

                # Lógica de Snap
                closest_widget, min_dist_sq = None, float("inf")
                
                if not self.focusable_widgets:
                    self._update_loop_job = self.after(50, self.update_loop)
                    return

                for widget in self.focusable_widgets:
                    if not widget.winfo_exists():
                        continue
                    x, y = widget.winfo_rootx(), widget.winfo_rooty()
                    w, h = widget.winfo_width(), widget.winfo_height()
                    center_x, center_y = x + w / 2, y + h / 2
                    dist_sq = (final_gaze_x - center_x) ** 2 + (final_gaze_y - center_y) ** 2
                    if dist_sq < min_dist_sq:
                        min_dist_sq, closest_widget = dist_sq, widget

                # Aplica Snap/Highlight
                if closest_widget and min_dist_sq ** 0.5 <= SNAP_THRESHOLD_PIXELS:
                    if closest_widget != self.currently_snapped_widget:
                        
                        # Ativa o Modo de Varredura
                        if closest_widget == self.keyboard_frame_widget:
                            print("[Scanner] Gaze entrou no teclado. Ativando.")
                            self.scan_mode_active = True
                            self.currently_snapped_widget = None
                            
                            self.scan_index = -1 
                            self.last_scan_time = time.time() - SCAN_DELAY_SECONDS
                            
                            try: 
                                if isinstance(closest_widget, tk.Frame):
                                    pass 
                                else:
                                    closest_widget.configure(highlightbackground="#0b4073", highlightthickness=3)
                            except: pass
                            
                            self._update_loop_job = self.after(50, self.update_loop)
                            return 
                        
                        pyautogui.moveTo(
                            closest_widget.winfo_rootx() + closest_widget.winfo_width() / 2,
                            closest_widget.winfo_rooty() + closest_widget.winfo_height() / 2,
                            duration=0.1
                        )
                        # Remove highlight antigo
                        if self.currently_snapped_widget and self.currently_snapped_widget.winfo_exists():
                            try:
                                if isinstance(self.currently_snapped_widget, tk.Text):
                                    self.currently_snapped_widget.configure(highlightbackground="white", highlightthickness=2)
                                else:
                                    self.currently_snapped_widget.configure(highlightbackground="#0b4073", highlightthickness=3)
                            except: pass
                        # Adiciona highlight novo
                        if closest_widget.winfo_exists():
                            try:
                                if isinstance(closest_widget, tk.Text):
                                    closest_widget.configure(highlightbackground="#00ff00", highlightthickness=4)
                                else:
                                    closest_widget.configure(highlightbackground="#00ff00", highlightthickness=6)
                            except: pass
                        self.currently_snapped_widget = closest_widget
                
                # Lógica de Free-Move (sem snap)
                else:
                    # O 'gaze_frozen' não existe mais, então o free-move está sempre ativo
                    now = time.time()
                    gaze_point = (final_gaze_x, final_gaze_y)
                    if self.last_gaze_pos is None:
                        self.last_gaze_pos = gaze_point
                        self.last_stable_time = now

                    dist = ((gaze_point[0] - self.last_gaze_pos[0]) ** 2 + (
                                gaze_point[1] - self.last_gaze_pos[1]) ** 2) ** 0.5

                    if dist > GAZE_TOLERANCE_PX:
                        self.last_gaze_pos = gaze_point
                        self.last_stable_time = now
                    elif now - self.last_stable_time >= GAZE_STABILITY_DELAY:
                        pyautogui.moveTo(gaze_point[0], gaze_point[1], duration=0.1)
                        self.last_cursor_pos = gaze_point
                    
                    # Remove highlight ao sair do foco
                    if self.currently_snapped_widget and self.currently_snapped_widget.winfo_exists():
                        try:
                            if isinstance(self.currently_snapped_widget, tk.Text):
                                self.currently_snapped_widget.configure(highlightbackground="white", highlightthickness=2)
                            else:
                                self.currently_snapped_widget.configure(highlightbackground="#0b4073", highlightthickness=3)
                        except: pass
                        self.currently_snapped_widget = None

        # Reagenda o loop
        self._update_loop_job = self.after(50, self.update_loop)

    # --------- Infra (Limpeza e Saída) ----------

    def _clear_root(self):
        self.is_navigating = True 

        if self._update_loop_job:
            self.after_cancel(self._update_loop_job)
            self._update_loop_job = None
        
        try:
            self.unbind("<Escape>")
        except tk.TclError:
            pass 

        if self.current_screen and hasattr(self.current_screen, "on_destroy"):
            self.current_screen.on_destroy()

        self._stop_camera_preview()

        # Reseta o estado do controller
        self.notepad_text_widget = None
        self.sticky_shift_active = False
        self.caps_lock_active = False
        self.focusable_widgets = []
        self.current_screen = None
        
        # Reseta o estado de varredura
        self.scan_mode_active = False
        self.keyboard_frame_widget = None
        self.scan_key_list = []
        self.scan_index = -1
        self.last_scan_time = 0
        self.is_dwell_clicking = False
        self.dwell_start_time = 0
        self.blink_pre_dwell_start_time = 0 # <-- NOVO
        
        # Reseta o estado de clique do dashboard
        self.blink_state = "IDLE"
        self.blink_start_time = 0

        for w in self.winfo_children():
            w.destroy()

    def quit_app(self):
        try:
            self._clear_root()
            if self.tracker:
                self.tracker.stop()
        finally:
            self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()