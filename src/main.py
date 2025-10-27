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
from ui.dashboard import DashboardFrame
import os

from tracking.eye_tracker import EyeTracker
from tracking import calibration

SNAP_THRESHOLD_PIXELS = 300
CAM_PROBE_MAX = 1        # quantos índices de câmera testar (0..3)
PREVIEW_SIZE = (320, 240)
GAZE_MOVE_DELAY = 5  # segundos de atraso no movimento do cursor
GAZE_STABILITY_DELAY = 1.5  # <--- ADICIONE ESTA LINHA
GAZE_TOLERANCE_PX = 80

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
        self.current_screen = None
        self._update_loop_job = None
        self._modal_widget_backup = []

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

        # --- Estado do preview contínuo ---
        self._preview_cap = None
        self._preview_job = None
        self._calib_cap = None
        self._calib_job = None

        # --- Estado do Bloco de Notas ---
        self.notepad_text_widget = None
        self.sticky_shift_active = False
        self.caps_lock_active = False
        self.notepad_is_dirty = False
        self.notepad_last_save_content = ""
        self.notepad_save_dir = os.path.join(os.path.expanduser("~"), "Documentos", "SimpleEyeTracker")

        # --- Referências de Ícones (carregar no __init__ para reuso) ---
        self.icon_home = None
        self.icon_notepad = None
        self._load_sidebar_icons()

        # Tela inicial no root (sem Toplevel/withdraw) — base funcional
        self._build_startup_frame()

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

    def abrir_configuracoes_view(self):
        """Versão de 'abrir_configuracoes' como um método da classe."""
        config = tk.Toplevel(self)
        config.title("Configurações")
        config.configure(bg="#222")
        config.resizable(False, False)
        tk.Label(config, text="Configurações", font=("Arial", 16, "bold"), bg="#222", fg="white").pack(pady=20)
        # Note a mudança aqui: chama o método do self (App)
        tk.Button(config, text="Trocar ou Gerenciar Perfis", command=self.create_calibrator_view).pack(pady=10)
        tk.Button(config, text="Fechar", command=config.destroy).pack(pady=20)
        # Posiciona no monitor ativo
        mon = self.get_active_monitor()
        config.geometry(f"400x300+{mon.x + 100}+{mon.y + 100}")
        config.transient(self)

    def create_notepad_view(self):
        """Cria a UI principal do editor de texto e teclado virtual."""
        self._clear_root()
        self.title("Bloco de Notas - Controle Ocular")
        cor_fundo = "#0b4073"  # Azul-escuro padrão
        self.configure(bg=cor_fundo)

        # --- Layout Principal ---
        main_frame = tk.Frame(self, bg=cor_fundo)
        main_frame.pack(fill="both", expand=True)

        # 1. Barra Lateral (Esquerda)
        sidebar_frame = tk.Frame(main_frame, bg="#083057", width=100)
        sidebar_frame.pack(side="left", fill="y", padx=(10, 0), pady=10)
        sidebar_frame.pack_propagate(False)  # Impede que os botões encolham a barra

        # 2. Área de Conteúdo (Direita)
        content_frame = tk.Frame(main_frame, bg=cor_fundo)
        content_frame.pack(side="left", fill="both", expand=True, padx=20, pady=10)

        # --- 1. Povoar Barra Lateral ---
        self._build_notepad_sidebar(sidebar_frame)

        # --- 2. Povoar Área de Conteúdo ---
        # 2a. Header (Botões Salvar/Novo)
        header_frame = tk.Frame(content_frame, bg=cor_fundo)
        header_frame.pack(fill="x", pady=(0, 10))

        btn_novo = tk.Button(header_frame, text="Novo Documento", font=("Arial", 16),
                             command=self._handle_new_document)
        btn_novo.pack(side="left", padx=10)

        btn_salvar = tk.Button(header_frame, text="Salvar", font=("Arial", 16),
                               command=self._handle_save_document)
        btn_salvar.pack(side="right", padx=10)

        self.focusable_widgets.extend([btn_novo, btn_salvar])

        # 2b. Área de Texto
        text_frame = tk.Frame(content_frame, bg="white", borderwidth=2, relief="solid")
        text_frame.pack(fill="both", expand=True)

        self.notepad_text_widget = tk.Text(text_frame, wrap="word", font=("Arial", 20),
                                           undo=True, bg="white", fg="black",
                                           insertbackground="black", relief="flat")
        self.notepad_text_widget.pack(fill="both", expand=True, padx=10, pady=10)
        self.focusable_widgets.append(self.notepad_text_widget)

        # Preenche com o conteúdo "não salvo" se estivermos apenas recarregando
        if self.notepad_last_save_content and self.notepad_is_dirty:
            self.notepad_text_widget.insert("1.0", self.notepad_last_save_content)
        else:
            self.notepad_last_save_content = ""  # Limpa para um novo doc

        # Rastreia alterações
        self.notepad_text_widget.bind("<<Modified>>", self._on_notepad_modified)

        # 2c. Teclado Virtual
        keyboard_frame = tk.Frame(content_frame, bg=cor_fundo, pady=10)
        keyboard_frame.pack(fill="x", side="bottom")
        self._build_virtual_keyboard(keyboard_frame)

        # --- 3. Barra de Status (Inferior) ---
        bottom_bar = tk.Frame(self, bg=cor_fundo)
        bottom_bar.pack(fill="x", pady=(0, 16))

        self.notepad_status_label = tk.Label(
            bottom_bar,
            text="Eye Tracking: DESATIVADO (Pressione F7)",
            font=("Poppins", 16, "bold"), bg=cor_fundo, fg="white"
        )
        self.notepad_status_label.pack(side="right", padx=20)
        # Atualiza o status caso já esteja ativo
        self._update_status_label()

        # --- 4. Bindings e Loop ---
        self.bind("<F7>", self.toggle_mouse_control)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.move_root_to_monitor()
        self.update_loop()

        # Foca o widget de texto por padrão
        self.notepad_text_widget.focus_set()

    def _build_notepad_sidebar(self, parent_frame):
        """Cria os botões da barra lateral para o notepad."""

        # Botão CASA (Voltar ao Dashboard)
        btn_home = tk.Button(parent_frame, image=self.icon_home, bg="#0b4073",
                             activebackground="#115a9e", relief="flat", borderwidth=0,
                             command=self.create_dashboard)  # Volta ao dashboard
        btn_home.pack(pady=(300, 20), fill="x", padx=10)

        # Botão NOTEPAD (Ativo)
        btn_notepad = tk.Button(parent_frame, image=self.icon_notepad, bg="#1E88E5",  # Cor de destaque
                                relief="flat", borderwidth=0, state="disabled")  # Desabilitado
        btn_notepad.pack(pady=10, fill="x", padx=10)

        self.focusable_widgets.append(btn_home)
        # Não adicionamos o btn_notepad por estar desabilitado

    def _build_virtual_keyboard(self, parent_frame):
        """Cria e exibe o teclado virtual."""

        key_style = {"font": ("Arial", 16), "bg": "#333", "fg": "white",
                     "activebackground": "#555", "activeforeground": "white",
                     "relief": "solid", "borderwidth": 1, "width": 4, "height": 1,
                     "padx": 5, "pady": 5}

        # Layout das teclas (QWERTY)
        key_rows = [
            ['`', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '=', 'Backspace'],
            ['Tab', 'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '[', ']', '\\'],
            ['Caps', 'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';', "'", 'Enter'],
            ['Shift', 'z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '/', 'Shift'],
            ['Space']
        ]

        for r_idx, row in enumerate(key_rows):
            row_frame = tk.Frame(parent_frame, bg=parent_frame.cget("bg"))
            row_frame.pack()

            for key_char in row:
                btn = tk.Button(row_frame, text=key_char, **key_style)

                # --- Lógica de Comando ---
                # A função lambda captura o valor de key_char no momento da criação
                cmd = lambda char=key_char: self._on_key_press(char)
                btn.configure(command=cmd)

                # --- Lógica de Tamanho ---
                if key_char == 'Space':
                    btn.configure(width=60)
                elif key_char in ['Backspace', 'Enter', 'Shift', 'Caps', 'Tab']:
                    btn.configure(width=8, bg="#555")  # Destaque para teclas especiais

                btn.pack(side="left", padx=2, pady=2)
                self.focusable_widgets.append(btn)

                # Guarda referência dos botões de estado
                if key_char == 'Shift':
                    self.shift_btn_ref = btn
                if key_char == 'Caps':
                    self.caps_btn_ref = btn

    def _on_key_press(self, key_char):
        """Lida com todos os cliques do teclado virtual."""
        if not self.notepad_text_widget:
            return

        widget = self.notepad_text_widget

        # 1. Teclas de Controle
        if key_char == 'Backspace':
            # Deleta o caractere antes do cursor
            widget.delete(tk.INSERT + "-1c", tk.INSERT)
        elif key_char == 'Enter':
            widget.insert(tk.INSERT, '\n')
        elif key_char == 'Tab':
            widget.insert(tk.INSERT, '\t')
        elif key_char == 'Space':
            widget.insert(tk.INSERT, ' ')

        # 2. Teclas Modificadoras (Sticky)
        elif key_char == 'Shift':
            self.sticky_shift_active = not self.sticky_shift_active
            # Feedback visual (opcional, mas recomendado)
            new_color = "#1E88E5" if self.sticky_shift_active else "#555"
            if hasattr(self, "shift_btn_ref"): self.shift_btn_ref.configure(bg=new_color)

        elif key_char == 'Caps':
            self.caps_lock_active = not self.caps_lock_active
            new_color = "#1E88E5" if self.caps_lock_active else "#555"
            if hasattr(self, "caps_btn_ref"): self.caps_btn_ref.configure(bg=new_color)

        # 3. Teclas de Caractere
        else:
            char_to_insert = key_char
            is_letter = key_char.isalpha() and len(key_char) == 1

            # Lógica XOR: (Caps ATIVADO e Shift DESATIVADO) ou (Caps DESATIVADO e Shift ATIVADO)
            is_upper = self.caps_lock_active ^ self.sticky_shift_active

            if is_letter:
                char_to_insert = key_char.upper() if is_upper else key_char.lower()

            widget.insert(tk.INSERT, char_to_insert)

            # Desativa o Shift (sticky) após o uso
            if self.sticky_shift_active:
                self.sticky_shift_active = False
                if hasattr(self, "shift_btn_ref"): self.shift_btn_ref.configure(bg="#555")

        widget.focus_set()  # Mantém o foco no texto
        widget.event_generate("<<Modified>>")  # Força a detecção de "dirty"

    def _on_notepad_modified(self, event=None):
        """Chamado quando o texto é alterado. Seta o flag 'dirty'."""
        self.notepad_is_dirty = True
        # Desativa o rastreador de "undo" do próprio widget
        # para que possamos rastrear a sujeira de forma confiável
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
            self.notepad_last_save_content = content  # Atualiza o "ponto salvo"
            self._show_custom_modal("Salvo", f"Documento salvo com sucesso em:\n{filename}")

        except Exception as e:
            messagebox.showerror("Erro ao Salvar", f"Não foi possível salvar o arquivo:\n{e}")

    def _handle_new_document(self):
        """Limpa o editor ou pede para salvar se houver alterações."""
        if not self.notepad_text_widget:
            return

        current_content = self.notepad_text_widget.get("1.0", tk.END)
        # Checa se está sujo (modificado) ou se está vazio (só com \n)
        is_empty = not current_content.strip()

        if is_empty or not self.notepad_is_dirty:
            self._clear_notepad()
        else:
            # Precisa de diálogo. Por simplicidade, vamos usar o messagebox padrão
            # (Um diálogo customizado controlável pelo olhar é bem mais complexo)
            resposta = messagebox.askyesnocancel("Novo Documento", "Deseja salvar as alterações?")

            if resposta is True:  # Sim
                self._handle_save_document()
                self._clear_notepad()
            elif resposta is False:  # Não
                self._clear_notepad()
            elif resposta is None:  # Cancelar
                pass  # Não faz nada

    def _clear_notepad(self):
        """Limpa o widget de texto e reseta os flags."""
        self.notepad_last_save_content = ""
        self.notepad_is_dirty = False
        if self.notepad_text_widget:
            self.notepad_text_widget.delete("1.0", tk.END)
            self.notepad_text_widget.edit_modified(False)  # Reseta o flag interno

    def _show_custom_modal(self, title, message):
        """Cria um Toplevel modal (pop-up) controlável pelo olhar."""

        # 1. Backup dos widgets focáveis da tela de fundo (teclado, botões, etc.)
        self._modal_widget_backup = self.focusable_widgets
        self.focusable_widgets = []
        self.currently_snapped_widget = None  # Limpa o snap da tela anterior

        # 2. Cria a janela do diálogo
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg="#222")
        dialog.resizable(False, False)

        # Centraliza no monitor ativo
        mon = self.get_active_monitor()
        w, h = 450, 220
        x = mon.x + (mon.width // 2 - w // 2)
        y = mon.y + (mon.height // 2 - h // 2)
        dialog.geometry(f"{w}x{h}+{x}+{y}")

        dialog.transient(self)  # Mantém no topo da aplicação
        dialog.grab_set()  # Bloqueia interações de mouse com outras janelas

        # 3. Adiciona conteúdo
        msg_label = tk.Label(dialog, text=message, bg="#222", fg="white",
                             font=("Arial", 14), wraplength=400, justify="center")
        msg_label.pack(pady=30, padx=20, expand=True)

        # 4. Botão "OK"
        def _close_dialog():
            self.focusable_widgets = self._modal_widget_backup  # Restaura widgets
            self._modal_widget_backup = []
            self.currently_snapped_widget = None
            dialog.grab_release()
            dialog.destroy()

        ok_button = tk.Button(dialog, text="OK", font=("Arial", 16, "bold"),
                              command=_close_dialog, width=10)
        ok_button.pack(pady=20)

        # 5. Adiciona o botão "OK" como o ÚNICO widget focável
        self.focusable_widgets.append(ok_button)

        # 6. Garante que, se o usuário fechar pelo "X", tudo é restaurado
        dialog.protocol("WM_DELETE_WINDOW", _close_dialog)

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
        # limpa tela
        self._clear_root()
        self.title("Dashboard - Controle Ocular")
        self.configure(bg="#0b4073")

        # 1. Cria a instância da View do Dashboard
        # A 'parent' é 'self' (a janela principal do App)
        # O 'controller' também é 'self' (a classe App com a lógica)
        dashboard_view = DashboardFrame(self, controller=self)
        dashboard_view.pack(fill="both", expand=True)

        # 2. Guarda a referência da tela atual e pega os widgets focáveis
        self.current_screen = dashboard_view
        self.focusable_widgets = dashboard_view.get_focusable_widgets()

        # 3. Bindings e ciclo (permanecem na lógica principal)
        self.bind("<F7>", self.toggle_mouse_control)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

        # 4. Garante que a janela ocupa o monitor selecionado
        self.move_root_to_monitor(self.selected_monitor_index)

        # 5. Atualiza o status (caso já esteja ativo) e inicia o loop
        self._update_status_label()  # Atualiza o label
        self.update_loop()

    def _load_sidebar_icons(self):
        """Carrega e redimensiona ícones para a barra lateral."""
        ICON_SIZE = (48, 48)
        # --- Substitua pelos caminhos reais dos seus ícones ---
        ICON_HOME_PATH = "resources/images/home.png"
        ICON_NOTEPAD_PATH = "resources/images/notepad.png"  # Usando o mesmo do tile

        try:
            img = Image.open(ICON_HOME_PATH).resize(ICON_SIZE, Image.LANCZOS)
            self.icon_home = ImageTk.PhotoImage(img)
        except Exception as e:
            print(f"Erro ao carregar ícone 'home': {e}")

        try:
            # Reusa o ícone do notepad, mas redimensiona
            img = Image.open(ICON_NOTEPAD_PATH).resize(ICON_SIZE, Image.LANCZOS)
            self.icon_notepad = ImageTk.PhotoImage(img)
        except Exception as e:
            print(f"Erro ao carregar ícone 'notepad' para sidebar: {e}")

    def _update_status_label(self):
        """Helper para atualizar o label de status na tela ativa."""
        state_text = "ATIVADO" if self.mouse_control_enabled else "DESATIVADO"
        full_text = f"Eye Tracking: {state_text} (Pressione F7)"

        try:
            # Caso 1: Estamos em uma "View" (como o Dashboard)
            if hasattr(self.current_screen, "update_status_label"):
                self.current_screen.update_status_label(full_text)

            # Caso 2: Estamos na tela do Notepad (que ainda não é uma View)
            elif hasattr(self, "notepad_status_label") and self.notepad_status_label:
                self.notepad_status_label.config(text=full_text)
        except tk.TclError:
            # Janela foi destruída no meio da atualização, ignora o erro
            pass

    def toggle_mouse_control(self, event=None):
        self.mouse_control_enabled = not self.mouse_control_enabled
        self._update_status_label()  # <-- CHAMA O HELPER

        if not self.mouse_control_enabled:
            self.currently_snapped_widget = None

    def update_loop(self):
        # 1. Checa se o controle está ativo
        if self.mouse_control_enabled and self.tracker:

            # 2. Checa se estamos no "congelamento" PÓS-clique
            now = time.time()
            if self.just_clicked_time and (now - self.just_clicked_time < GAZE_MOVE_DELAY):
                # Pausa o movimento do cursor pelo tempo de GAZE_MOVE_DELAY
                self._update_loop_job = self.after(50, self.update_loop)
                return
            # Reseta o flag se o tempo já passou
            self.just_clicked_time = 0

            lock = self.shared_state.get("_lock")
            if lock is None:
                self._update_loop_job = self.after(50, self.update_loop)
                return

            # --- LÓGICA DE CLIQUE ATUALIZADA ---
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

                # --- LÓGICA DE EXECUÇÃO MELHORADA ---
                try:
                    # Caso 1: É um botão, invoca a ação
                    widget.invoke()
                except tk.TclError:
                    # Caso 2: Não é um botão (ex: Label, Text, Frame)
                    # Apenas foca nele.
                    widget.focus_set()
                except Exception as e:
                    # Outro erro inesperado, ignora
                    print(f"[EyeTracker] Erro no clique ocular (invoke): {e}")
                    pass

                # Executa o clique do mouse e congela a tela, SEMPRE
                pyautogui.click()
                self.just_clicked_time = time.time()
                # --- FIM DA LÓGICA MELHORADA ---

                # Pula o resto da lógica de movimento para este frame
                self._update_loop_job = self.after(50, self.update_loop)
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

                # Proteção: só itera se a lista não for vazia
                if not self.focusable_widgets:
                    self._update_loop_job = self.after(50, self.update_loop)
                    return

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
                        if self.currently_snapped_widget and self.currently_snapped_widget.winfo_exists():
                            try:
                                # Lógica para Text vs Botão
                                if isinstance(self.currently_snapped_widget, tk.Text):
                                    self.currently_snapped_widget.configure(highlightbackground="white",
                                                                            highlightthickness=2)
                                else:
                                    self.currently_snapped_widget.configure(highlightbackground="#0b4073",
                                                                            highlightthickness=3)
                            except:
                                pass

                        if closest_widget.winfo_exists():
                            try:
                                # Lógica para Text vs Botão
                                if isinstance(closest_widget, tk.Text):
                                    closest_widget.configure(highlightbackground="#00ff00", highlightthickness=4)
                                else:
                                    closest_widget.configure(highlightbackground="#00ff00", highlightthickness=6)
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
                    if self.currently_snapped_widget and self.currently_snapped_widget.winfo_exists():
                        try:
                            if isinstance(self.currently_snapped_widget, tk.Text):
                                self.currently_snapped_widget.configure(highlightbackground="white",
                                                                        highlightthickness=2)
                            else:
                                self.currently_snapped_widget.configure(highlightbackground="#0b4073",
                                                                        highlightthickness=3)
                        except:
                            pass
                        self.currently_snapped_widget = None

        # Reagenda o loop
        self._update_loop_job = self.after(50, self.update_loop)

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

        if self._update_loop_job:
            self.after_cancel(self._update_loop_job)
            self._update_loop_job = None

        self._stop_camera_preview()
        self._stop_calib_preview()
        self.notepad_text_widget = None
        self.sticky_shift_active = False
        self.caps_lock_active = False
        self.notepad_status_label = None
        self.focusable_widgets = [] # Limpa widgets focáveis
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