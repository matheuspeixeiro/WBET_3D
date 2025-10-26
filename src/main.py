# src/main.py
# Versão 8 (funcional + preview contínuo de câmera):
# - Tela inicial no root para escolher Monitor e Câmera (com preview AO VIVO)
# - Tela de calibração também com preview AO VIVO da câmera escolhida
# - Previews liberam a câmera automaticamente ao trocar de tela ou iniciar calibração
# - Todas as janelas abrem no monitor selecionado
# - Suporte a múltiplos monitores + offset correto do gaze
# - Loop de atualização resiliente com RLock
# - CRUD de perfis estável (carregar/criar) sem travar UI

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

from tracking.eye_tracker import EyeTracker
from tracking import calibration

SNAP_THRESHOLD_PIXELS = 300
CAM_PROBE_MAX = 1        # quantos índices de câmera testar (0..3)
PREVIEW_SIZE = (320, 240)
GAZE_MOVE_DELAY = 5  # segundos de atraso no movimento do cursor
GAZE_STABILITY_DELAY = 1.5  # <--- ADICIONE ESTA LINHA
GAZE_TOLERANCE_PX = 80

# --- Funções Auxiliares (Apps) ---
def abrir_bloco_de_notas(app_instance):
    """Abre um editor de texto simples, com fallback para Tkinter, no monitor ativo."""
    try:
        if sys.platform.startswith("darwin"):
            subprocess.Popen(["open", "-a", "TextEdit"])
            return
        elif sys.platform.startswith("win"):
            subprocess.Popen(["notepad.exe"])
            return
        else:
            for editor in ["gedit", "xed", "kate", "mousepad"]:
                if shutil.which(editor):
                    subprocess.Popen([editor])
                    return
    except Exception:
        pass

    # --- Fallback integrado ---
    editor = tk.Toplevel(app_instance)
    editor.title("Bloco de Notas Interno")
    text_area = tk.Text(editor, wrap="word", font=("Arial", 14))
    text_area.pack(expand=True, fill="both")
    # Posiciona no monitor ativo
    mon = app_instance.get_active_monitor()
    editor.geometry(f"800x600+{mon.x + 60}+{mon.y + 60}")
    editor.transient(app_instance)


def abrir_configuracoes(app_instance):
    config = tk.Toplevel(app_instance)
    config.title("Configurações")
    config.configure(bg="#222")
    config.resizable(False, False)
    tk.Label(config, text="Configurações", font=("Arial", 16, "bold"), bg="#222", fg="white").pack(pady=20)
    tk.Button(config, text="Trocar ou Gerenciar Perfis", command=app_instance.create_calibrator_view).pack(pady=10)
    tk.Button(config, text="Fechar", command=config.destroy).pack(pady=20)
    # Posiciona no monitor ativo
    mon = app_instance.get_active_monitor()
    config.geometry(f"400x300+{mon.x + 100}+{mon.y + 100}")
    config.transient(app_instance)

def _criar_tile(parent, image_path=None, command=None, border="#0b4073"):
    """
    Cria um 'tile' grande (quadrado/retângulo) de dashboard.
    - Se command for None: o tile é 'inert' (slot vazio).
    - Se image_path existir: ícone centralizado grande.
    Retorna o widget 'superfície' que entra em focusable_widgets.
    """
    cont = tk.Frame(parent, bg="#ffffff", highlightthickness=3, highlightbackground=border)
    # Superfície clicável (Button se funcional, Label se slot vazio)
    surface_kwargs = dict(bg="#ffffff", activebackground="#f2f6ff", bd=0, relief="flat")
    if command:
        surface = tk.Button(cont, **surface_kwargs, command=command)
    else:
        surface = tk.Label(cont, **surface_kwargs)

    surface.pack(expand=True, fill="both", padx=12, pady=12)

    # Ícone central
    if image_path:
        try:
            img = Image.open(image_path)
            # tamanho alvo grande mas seguro (ajustável)
            img = img.resize((220, 220))
            icon = ImageTk.PhotoImage(img)
            # guardamos referência pra não ser coletado
            surface._icon_ref = icon
            surface.configure(image=icon, compound="center")
        except Exception:
            # se der erro no ícone, deixamos só o bloco branco
            pass

    return cont, surface

# --- CLASSE PRINCIPAL ---
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        pyautogui.FAILSAFE = False  # evita exceção de failsafe no canto (0,0)

        self.last_gaze_move_time = 0
        self.last_gaze_pos = None
        self.last_stable_time = time.time()
        self.last_cursor_pos = None
        self.just_clicked_time = 0  # <--- ADICIONE ESTA LINHA
        self.title("Assistente de Acessibilidade Ocular")
        self.configure(bg="#222")
        self.minsize(560, 520)

        # Estado do app
        self.tracker = None
        self.shared_state = {"_lock": threading.RLock()}  # RLock evita deadlocks
        self.mouse_control_enabled = False
        self.focusable_widgets = []
        self.currently_snapped_widget = None
        self.selected_monitor_index = 0
        self.default_camera_index = 0

        # Monitores (com fallback)
        self.available_monitors = self._get_monitores_com_fallback()
        print("Monitores detectados:")
        for i, m in enumerate(self.available_monitors):
            print(f"  {i}: {m.width}x{m.height} @ ({m.x},{m.y})")

        # Câmeras detectadas (labels amigáveis)
        self._camera_list = self._probe_cameras(CAM_PROBE_MAX)  # [{'index':0, 'label':'Câmera 0 (1280x720)'}, ...]

        # --- Estado do preview contínuo ---
        self._preview_cap = None
        self._preview_job = None
        self._calib_cap = None
        self._calib_job = None

        # Tela inicial no root (sem Toplevel/withdraw) — base funcional
        self._build_startup_frame()

    # -------- Utilidades de monitor / janela ----------
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

    # --------- UI: Tela inicial ----------
    def _build_startup_frame(self):
        """Tela inicial simples dentro do root para escolher monitor e câmera, com preview contínuo."""
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

        # Câmeras detectadas + nomes
        cam_labels = [c["label"] for c in self._camera_list] or ["Câmera 0"]
        tk.Label(frame, text="Câmera:", font=("Arial", 14), bg="#222", fg="white").pack()
        self.start_camera_var = tk.StringVar(value=cam_labels[0])
        cam_menu = tk.OptionMenu(frame, self.start_camera_var, *cam_labels, command=self._on_start_cam_change)
        cam_menu.pack(pady=(5, 10))

        # Preview contínuo (ao vivo)
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

        # Centraliza a janela inicial no monitor 0 para o usuário ver
        mon0 = self.available_monitors[0]
        w, h = 600, 560
        x = mon0.x + (mon0.width - w) // 2
        y = mon0.y + (mon0.height - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _probe_cameras(self, max_test=4):
        """Tenta abrir os índices 0..max_test-1, faz um snapshot para inferir label amigável."""
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

    # --- Preview contínuo: tela inicial ---
    def _on_start_cam_change(self, selected_label):
        if not self._camera_list:
            return
        cam = next((c for c in self._camera_list if c["label"] == selected_label), self._camera_list[0])
        self._start_camera_preview(cam["index"])

    def _start_camera_preview(self, cam_index: int):
        """Inicia/retoma o preview contínuo da câmera na tela inicial."""
        self._stop_camera_preview()
        self._preview_cap = cv2.VideoCapture(cam_index)
        self._preview_loop()

    def _preview_loop(self):
        """Loop de atualização do preview (tela inicial)."""
        if not self._preview_cap:
            return
        ret, frame = self._preview_cap.read()
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame).resize(PREVIEW_SIZE)
            imgtk = ImageTk.PhotoImage(img)
            self.preview_holder.configure(image=imgtk, text="")
            self.preview_holder.image = imgtk
        # agenda próximo frame (~30 fps)
        self._preview_job = self.after(33, self._preview_loop)

    def _stop_camera_preview(self):
        """Para o loop e fecha a câmera (tela inicial)."""
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
        # parar preview contínuo da tela inicial antes de trocar de tela
        self._stop_camera_preview()

        try:
            self.selected_monitor_index = int(self.start_monitor_var.get().split()[1])
        except Exception:
            self.selected_monitor_index = 0

        # pega índice da câmera pelo label
        if self._camera_list:
            sel_label = self.start_camera_var.get()
            cam = next((c for c in self._camera_list if c["label"] == sel_label), self._camera_list[0])
            self.default_camera_index = cam["index"]
        else:
            self.default_camera_index = 0

        # Move root para o monitor escolhido e segue para a tela de perfis/calibração
        self.move_root_to_monitor(self.selected_monitor_index)
        self.create_calibrator_view()

    # --------- UI: Tela de calibração e perfis ----------
    def create_calibrator_view(self):
        self._clear_root()
        self.configure(bg="#222")

        frame = tk.Frame(self, bg="#222")
        frame.pack(expand=True)

        tk.Label(frame, text="Selecione um Perfil ou Crie um Novo",
                 font=("Arial", 26, "bold"), bg="#222", fg="white").pack(pady=30)

        profiles = calibration.list_profiles()
        if profiles:
            tk.Label(frame, text="Carregar Perfil Existente:",
                     font=("Arial", 16), bg="#222", fg="white").pack(pady=(20, 5))
            self.profile_var = tk.StringVar(self)
            self.profile_var.set(profiles[0])
            tk.OptionMenu(frame, self.profile_var, *profiles).pack(pady=10)
            tk.Button(frame, text="Carregar Perfil", font=("Arial", 18),
                      command=self.load_profile_and_start).pack(pady=20)

        tk.Label(frame, text="Ou crie um novo perfil:",
                 font=("Arial", 16), bg="#222", fg="white").pack(pady=(40, 5))

        # Seleção de monitor para a calibração (pré-selecionado no ativo)
        tk.Label(frame, text="Monitor para Calibração:",
                 font=("Arial", 14), bg="#222", fg="white").pack(pady=(10, 5))
        monitor_options = [f"Monitor {i} ({m.width}x{m.height})" for i, m in enumerate(self.available_monitors)]
        self.monitor_var = tk.StringVar(self)
        self.monitor_var.set(monitor_options[self.selected_monitor_index])
        tk.OptionMenu(frame, self.monitor_var, *monitor_options).pack(pady=10)

        # Câmera (pré-selecionada)
        tk.Label(frame, text="Câmera para Calibração:", font=("Arial", 14), bg="#222", fg="white").pack(pady=(10, 5))
        cam_labels = [c["label"] for c in self._camera_list]
        self.camera_var = tk.StringVar(self)
        default_label = next((c["label"] for c in self._camera_list if c["index"] == self.default_camera_index),
                             (cam_labels[0] if cam_labels else "Câmera 0"))
        self.camera_var.set(default_label)

        # handler para trocar preview contínuo na calibração
        def _on_calib_cam_change(newlabel):
            self.camera_var.set(newlabel)
            cam = next((c for c in self._camera_list if c["label"] == newlabel), None)
            if cam:
                self._start_calib_preview(cam["index"])

        tk.OptionMenu(frame, self.camera_var, *cam_labels, command=_on_calib_cam_change).pack(pady=10)

        tk.Button(frame, text="Criar Novo Perfil e Calibrar",
                  font=("Arial", 18), command=self.run_calibration).pack(pady=20)

        # Preview contínuo também aqui (ajuda a confirmar a câmera)
        self.calib_preview = tk.Label(frame, bg="#000")
        self.calib_preview.pack(pady=(6, 0))
        sel_label = self.camera_var.get()
        cam = next((c for c in self._camera_list if c["label"] == sel_label), None)
        if cam:
            self._start_calib_preview(cam["index"])

    # --- Preview contínuo: tela de calibração ---
    def _start_calib_preview(self, cam_index: int):
        self._stop_calib_preview()
        self._calib_cap = cv2.VideoCapture(cam_index)
        self._calib_preview_loop()

    def _calib_preview_loop(self):
        if not self._calib_cap:
            return
        ret, frame = self._calib_cap.read()
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame).resize(PREVIEW_SIZE)
            imgtk = ImageTk.PhotoImage(img)
            self.calib_preview.configure(image=imgtk, text="")
            self.calib_preview.image = imgtk
        self._calib_job = self.after(33, self._calib_preview_loop)

    def _stop_calib_preview(self):
        if self._calib_job is not None:
            self.after_cancel(self._calib_job)
            self._calib_job = None
        if self._calib_cap is not None:
            try:
                self._calib_cap.release()
            except Exception:
                pass
            self._calib_cap = None

    def load_profile_and_start(self):
        profile_name = self.profile_var.get()
        calib_data = calibration.load_profile(profile_name)
        if not calib_data:
            messagebox.showerror("Erro", f"Não foi possível carregar o perfil '{profile_name}'.")
            return

        self.selected_monitor_index = int(calib_data.get("monitor_index", self.selected_monitor_index))
        camera_index = int(calib_data.get("camera_index", self.default_camera_index))
        self.move_root_to_monitor(self.selected_monitor_index)

        # Libera qualquer preview antes de iniciar o tracker
        self._stop_camera_preview()
        self._stop_calib_preview()

        self.tracker = EyeTracker(camera_index=camera_index, shared_state=self.shared_state)
        self.tracker.load_calibration(calib_data, profile_name)
        self.tracker.start()
        self.create_dashboard()

    def run_calibration(self):
        # pega seleção atual de monitor e câmera
        try:
            self.selected_monitor_index = int(self.monitor_var.get().split()[1])
        except Exception:
            pass

        cam_label = self.camera_var.get()
        cam = next((c for c in self._camera_list if c["label"] == cam_label), None)
        camera_index = cam["index"] if cam else self.default_camera_index

        self.move_root_to_monitor(self.selected_monitor_index)

        # janela de nome do perfil centralizada no monitor selecionado:
        profile_name = self._ask_profile_name(self.get_active_monitor())
        if not profile_name:
            return

        messagebox.showinfo(
            "Instruções",
            "Pressione 'C' para travar, 'S' para calibrar o centro, e 'Q' para sair."
        )

        # Antes de abrir a janela de debug, libera a câmera do preview
        self._stop_calib_preview()
        self._stop_camera_preview()

        # Inicia tracker e janela de debug no monitor escolhido
        self.tracker = EyeTracker(camera_index=camera_index, shared_state=self.shared_state)
        mon = self.get_active_monitor()
        self.tracker.start_debug_window(window_pos=(mon.x + 80, mon.y + 80))

        # Salva calibração
        calib_data = self.tracker.save_calibration()
        if calib_data:
            calib_data["monitor_index"] = self.selected_monitor_index
            calib_data["camera_index"] = camera_index
            calibration.save_profile(profile_name, calib_data)

        # Inicia o loop de rastreamento após calibrar
        self.tracker.start()
        self.tracker.loaded_profile_name = profile_name
        self.create_dashboard()

    def create_dashboard(self):
        # limpa tela e prepara estilos
        self._clear_root()
        self.title("Dashboard - Controle Ocular")
        cor_fundo = "#0b4073"  # azul-escuro do app
        self.configure(bg=cor_fundo)

        # Header com perfil + monitor
        active_profile = "Nenhum (Calibração Volátil)"
        if self.tracker and getattr(self.tracker, "loaded_profile_name", None):
            active_profile = self.tracker.loaded_profile_name
        mon = self.get_active_monitor()

        header = tk.Frame(self, bg=cor_fundo)
        header.pack(fill="x", padx=20, pady=(10, 0))

        # --- TÍTULO ADICIONADO ---
        tk.Label(header,
                 text="Simple Eye Tracker",
                 font=("Poppins", 22, "bold"),
                 bg=cor_fundo, fg="white").pack(side="left")
        # --- FIM DA ADIÇÃO ---

        # Label do perfil modificado para alinhar à direita
        tk.Label(header,
                 text=f"Perfil: {active_profile}   |   Monitor: {self.selected_monitor_index} ({mon.width}x{mon.height})",
                 font=("Poppins", 14), bg=cor_fundo, fg="white").pack(side="right")  # <--- MUDANÇA AQUI

        # Área central: grade 2x2 de tiles grandes
        grid_frame = tk.Frame(self, bg=cor_fundo)
        grid_frame.pack(expand=True, fill="both", padx=24, pady=24)

        # Configura grade responsiva 2x2
        for r in range(2):
            grid_frame.rowconfigure(r, weight=1, uniform="row")
        for c in range(2):
            grid_frame.columnconfigure(c, weight=1, uniform="col")

        # Caminhos dos ícones (como você pediu)
        ICON_NOTEPAD = "resources/images/notepad.png"
        ICON_SETTINGS = "resources/images/settings.png"

        # Tile (0,0) — Notepad (funcional)
        t00, surf00 = _criar_tile(
            grid_frame,
            image_path=ICON_NOTEPAD,
            command=lambda: abrir_bloco_de_notas(self)
        )
        t00.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)

        # Tile (0,1) — Configurações (funcional)
        t01, surf01 = _criar_tile(
            grid_frame,
            image_path=ICON_SETTINGS,
            command=lambda: abrir_configuracoes(self)
        )
        t01.grid(row=0, column=1, sticky="nsew", padx=16, pady=16)

        # Tile (1,0) — Slot vazio (inert)
        t10, surf10 = _criar_tile(
            grid_frame,
            image_path=None,  # sem ícone por enquanto
            command=None  # slot não funcional
        )
        t10.grid(row=1, column=0, sticky="nsew", padx=16, pady=16)

        # Tile (1,1) — Slot vazio (inert)
        t11, surf11 = _criar_tile(
            grid_frame,
            image_path=None,
            command=None
        )
        t11.grid(row=1, column=1, sticky="nsew", padx=16, pady=16)

        # Widgets "focáveis" para o snap (incluímos todos os quatro blocos)
        # — os dois de baixo não têm ação, mas ajudam o cursor a "colar" em áreas grandes.
        self.focusable_widgets = [surf00, surf01, surf10, surf11]

        # Barra inferior: status + dica F7
        bottom = tk.Frame(self, bg=cor_fundo)
        bottom.pack(fill="x", pady=(0, 16))

        # --- BLOCO DO BOTÃO DE PRÉVIA REMOVIDO ---

        # Status Eye Tracking
        self.status_label = tk.Label(
            bottom,
            text="Eye Tracking: DESATIVADO (Pressione F7)",
            font=("Poppins", 16, "bold"), bg=cor_fundo, fg="white"
        )
        # --- MUDANÇA: Centraliza o status se for o único item ---
        # (Se quiser ele na direita, mantenha .pack(side="right", padx=20))
        self.status_label.pack(side="right", padx=20)

        # Bindings e ciclo
        self.bind("<F7>", self.toggle_mouse_control)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

        # Garante que a janela ocupa o monitor selecionado
        self.move_root_to_monitor(self.selected_monitor_index)

        # Inicia o loop do controle ocular
        self.update_loop()

    # --------- Controle do mouse/gaze ----------
    def toggle_mouse_control(self, event=None):
        self.mouse_control_enabled = not self.mouse_control_enabled
        state_text = "ATIVADO" if self.mouse_control_enabled else "DESATIVADO"
        self.status_label.config(text=f"Eye Tracking: {state_text} (Pressione F7)")
        if not self.mouse_control_enabled:
            self.currently_snapped_widget = None

    def update_loop(self):
        # 1. Checa se o controle está ativo
        if self.mouse_control_enabled and self.tracker:

            # 2. Checa se estamos no "congelamento" PÓS-clique
            now = time.time()
            if self.just_clicked_time and (now - self.just_clicked_time < GAZE_MOVE_DELAY):
                # Pausa o movimento do cursor pelo tempo de GAZE_MOVE_DELAY
                self.after(50, self.update_loop)
                return
            # Reseta o flag se o tempo já passou
            self.just_clicked_time = 0

            lock = self.shared_state.get("_lock")
            if lock is None:
                self.after(50, self.update_loop)
                return

            # --- LÓGICA CORRIGIDA ---
            # 3. Pega o pedido de clique PRIMEIRO
            click_request = False
            with lock:
                click_request = self.shared_state.get("click_request", False)

            # 4. Se houver clique E um widget estiver focado, EXECUTA O CLIQUE
            if click_request and self.currently_snapped_widget:
                widget = self.currently_snapped_widget
                print(
                    f"[EyeTracker] Clique ocular em: {widget.cget('text') if hasattr(widget, 'cget') else widget}")

                # feedback visual (flash rápido de verde-claro)
                try:
                    original_color = widget.cget("highlightbackground")
                    widget.configure(highlightbackground="#00ff88", highlightthickness=8)
                    widget.after(150,
                                 lambda: widget.configure(highlightbackground=original_color, highlightthickness=6))
                except:
                    pass

                # consome o clique
                with lock:
                    self.shared_state["click_request"] = False

                # executa a ação do botão
                try:
                    widget.invoke()  # funciona para Button
                    pyautogui.click()
                    self.just_clicked_time = time.time()  # Ativa o congelamento PÓS-clique
                except:
                    # se for Label (slot vazio), ignora
                    pass

                # Pula o resto da lógica de movimento para este frame
                self.after(50, self.update_loop)
                return

            # 5. Se NÃO HOUVE CLIQUE, processa o movimento do olhar normalmente
            # (Limpa o flag de clique se ele não foi usado, ex: piscou fora de um alvo)
            if click_request:
                with lock:
                    self.shared_state["click_request"] = False

            # lê flags/valores rápidos com lock
            with lock:
                is_frozen = self.shared_state.get("gaze_frozen", False)
                frozen = self.shared_state.get("frozen_gaze_coords") if is_frozen else None

            # fora do lock, pega gaze do tracker (evita deadlock)
            gaze_data = frozen if is_frozen else self.tracker.get_screen_gaze()

            if gaze_data:
                gaze_x, gaze_y, _, _, _ = gaze_data

                mon = self.get_active_monitor()
                final_gaze_x = gaze_x + mon.x
                final_gaze_y = gaze_y + mon.y

                # Snap para botões "grandes"
                closest_widget, min_dist_sq = None, float("inf")
                for widget in self.focusable_widgets:
                    if not widget.winfo_exists():
                        continue
                    x = widget.winfo_rootx()
                    y = widget.winfo_rooty()
                    w = widget.winfo_width()
                    h = widget.winfo_height()
                    center_x, center_y = x + w / 2, y + h / 2
                    dist_sq = (final_gaze_x - center_x) ** 2 + (final_gaze_y - center_y) ** 2
                    if dist_sq < min_dist_sq:
                        min_dist_sq, closest_widget = dist_sq, widget

                if closest_widget and min_dist_sq ** 0.5 <= SNAP_THRESHOLD_PIXELS:
                    if closest_widget != self.currently_snapped_widget:
                        # --- move o cursor ---
                        pyautogui.moveTo(
                            closest_widget.winfo_rootx() + closest_widget.winfo_width() / 2,
                            closest_widget.winfo_rooty() + closest_widget.winfo_height() / 2,
                            duration=0.1
                        )

                        # --- destaca o widget atual ---
                        if self.currently_snapped_widget:
                            try:
                                self.currently_snapped_widget.configure(highlightbackground="#0b4073",
                                                                        highlightthickness=3)
                            except:
                                pass

                        try:
                            closest_widget.configure(highlightbackground="#00ff00",
                                                     highlightthickness=6)  # verde forte
                        except:
                            pass

                        self.currently_snapped_widget = closest_widget
                else:
                    if not is_frozen:
                        now = time.time()
                        gaze_point = (final_gaze_x, final_gaze_y)

                        if self.last_gaze_pos is None:
                            self.last_gaze_pos = gaze_point
                            self.last_stable_time = now

                        # mede distância entre o novo ponto e o último
                        dist = ((gaze_point[0] - self.last_gaze_pos[0]) ** 2 + (
                                gaze_point[1] - self.last_gaze_pos[1]) ** 2) ** 0.5

                        if dist > GAZE_TOLERANCE_PX:
                            # o olhar se moveu demais — reinicia o cronômetro
                            self.last_gaze_pos = gaze_point
                            self.last_stable_time = now
                        elif now - self.last_stable_time >= GAZE_STABILITY_DELAY:
                            # olhar estável o suficiente: move o cursor
                            pyautogui.moveTo(gaze_point[0], gaze_point[1], duration=0.1)
                            self.last_cursor_pos = gaze_point

                    # remove destaque se sair do foco
                    if self.currently_snapped_widget:
                        try:
                            self.currently_snapped_widget.configure(highlightbackground="#0b4073",
                                                                    highlightthickness=3)
                        except:
                            pass
                        self.currently_snapped_widget = None

        # Reagenda o loop
        self.after(50, self.update_loop)

        # --------- Diálogo custom p/ nome do perfil ----------

    # --------- Diálogo custom p/ nome do perfil ----------
    def _ask_profile_name(self, monitor):
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

    # --------- Infra ----------
    def _clear_root(self):
        # interrompe quaisquer previews ao trocar de tela
        self._stop_camera_preview()
        self._stop_calib_preview()
        for w in self.winfo_children():
            w.destroy()

    def quit_app(self):
        try:
            self._stop_camera_preview()
            self._stop_calib_preview()
            if self.tracker:
                self.tracker.stop()
        finally:
            self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()