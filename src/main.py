# main.py
# Versão 4: Dashboard azul com ícones grandes e layout acessível (estilo TCC)

import tkinter as tk
from tkinter import messagebox
import threading
import subprocess
import sys
import shutil
from PIL import Image, ImageTk
import pyautogui

from tracking.eye_tracker import EyeTracker

# --- CONSTANTE DE CONTROLE DE USABILIDADE ---
SNAP_THRESHOLD_PIXELS = 150


# --- Funções Auxiliares (Apps) ---
def abrir_bloco_de_notas():
    """Abre um editor de texto simples, com fallback para Tkinter."""
    if sys.platform.startswith("darwin"):
        subprocess.Popen(["open", "-a", "TextEdit"])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["notepad.exe"])
    else:
        for editor in ["gedit", "xed", "kate", "mousepad"]:
            if shutil.which(editor):
                subprocess.Popen([editor])
                return
        editor = tk.Toplevel()
        editor.title("Bloco de Notas (Interno)")
        text = tk.Text(editor, wrap="word", font=("Arial", 14))
        text.pack(expand=True, fill="both")
        editor.geometry("600x400")


def abrir_configuracoes():
    """Abre janela de configurações placeholder."""
    config = tk.Toplevel()
    config.title("Configurações")
    config.geometry("400x300")
    tk.Label(config, text="Configurações (placeholder)", font=("Arial", 16)).pack(pady=20)
    tk.Checkbutton(config, text="Ativar sons de feedback").pack(anchor="w", padx=20, pady=5)
    tk.Button(config, text="Fechar", command=config.destroy).pack(pady=20)


# --- Classe Principal da Aplicação ---
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Assistente de Acessibilidade Ocular")
        self.geometry("1280x720")

        self.tracker = None
        self.shared_state = {"_lock": threading.Lock()}
        self.mouse_control_enabled = False

        self.focusable_widgets = []
        self.currently_snapped_widget = None

        self.create_calibrator_view()

    # --- Tela de Calibração ---
    def create_calibrator_view(self):
        for widget in self.winfo_children():
            widget.destroy()

        self.configure(bg="#222")
        frame = tk.Frame(self, bg="#222")
        frame.pack(expand=True)

        tk.Label(frame, text="Selecione a Câmera e Calibre", font=("Arial", 26, "bold"), bg="#222", fg="white").pack(pady=30)

        self.camera_var = tk.StringVar(self)
        self.camera_var.set("Câmera 0")
        tk.OptionMenu(frame, self.camera_var, "Câmera 0", "Câmera 1").pack(pady=10)

        tk.Button(frame, text="Iniciar Calibração", font=("Arial", 18), command=self.run_calibration).pack(pady=40)

    def run_calibration(self):
        """Inicia o EyeTracker e abre a janela de calibração."""
        camera_index = int(self.camera_var.get().split()[-1])

        messagebox.showinfo(
            "Instruções de Calibração",
            "1. Olhe para o centro da tela e pressione 'C' para travar o monitor.\n"
            "2. Pressione 'S' para calibrar o centro do olhar.\n"
            "3. Pressione 'Q' para fechar a janela quando terminar."
        )

        self.tracker = EyeTracker(camera_index=camera_index, shared_state=self.shared_state)
        self.tracker.start_debug_window()
        self.tracker.start()

        self.create_dashboard()

    def create_dashboard(self):
        """Cria o dashboard principal em estilo moderno e acessível."""
        for widget in self.winfo_children():
            widget.destroy()

        self.title("Dashboard - Controle Ocular")
        self.configure(bg="#0b4073")

        # --- Fonte e cores principais ---
        fonte_titulo = ("Poppins", 40, "bold")
        fonte_botao = ("Poppins", 22, "bold")
        cor_fundo = "#0b4073"
        cor_botao = "#ffffff"
        cor_texto_botao = "#0b4073"
        cor_hover = "#cce1ff"

        # --- Título ---
        tk.Label(self, text="SISTEMA DE CONTROLE OCULAR",
                 font=fonte_titulo, bg=cor_fundo, fg="white").pack(pady=(60, 40))

        main_frame = tk.Frame(self, bg=cor_fundo)
        main_frame.pack(expand=True)

        # --- Função auxiliar para criar botões grandes com ícones ---
        def create_big_button(parent, text, icon_path, command):
            container = tk.Frame(parent, bg=cor_fundo)
            container.pack(pady=60)

            try:
                icon = Image.open(icon_path).resize((120, 120))
                icon_tk = ImageTk.PhotoImage(icon)
                btn = tk.Button(container,
                                image=icon_tk,
                                text=text,
                                compound="top",
                                font=fonte_botao,
                                fg=cor_texto_botao,
                                bg=cor_botao,
                                activebackground=cor_hover,
                                relief="flat",
                                bd=0,
                                width=320,
                                height=260,
                                highlightthickness=0,
                                command=command)
                btn.image = icon_tk
            except Exception:
                btn = tk.Button(container,
                                text=text,
                                font=fonte_botao,
                                fg=cor_texto_botao,
                                bg=cor_botao,
                                activebackground=cor_hover,
                                relief="flat",
                                bd=0,
                                width=20,
                                height=3,
                                command=command)

            # --- Efeito visual de hover (para simular destaque ao olhar) ---
            def on_enter(e):
                btn.config(bg=cor_hover)

            def on_leave(e):
                btn.config(bg=cor_botao)

            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)

            btn.pack(padx=20, pady=10)
            return btn

        # --- Botões principais ---
        btn_notepad = create_big_button(main_frame, "Bloco de Notas", "icons/notepad.png", abrir_bloco_de_notas)
        btn_config = create_big_button(main_frame, "Configurações", "icons/config.png", abrir_configuracoes)

        self.focusable_widgets = [btn_notepad, btn_config]

        # --- Status inferior ---
        self.status_label = tk.Label(self,
                                     text="Eye Tracking: DESATIVADO (Pressione F7)",
                                     font=("Poppins", 16, "bold"),
                                     bg=cor_fundo,
                                     fg="white")
        self.status_label.pack(pady=20, side="bottom")

        self.bind("<F7>", self.toggle_mouse_control)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.update_loop()

    # --- Controle de Rastreamento ---
    def toggle_mouse_control(self, event=None):
        self.mouse_control_enabled = not self.mouse_control_enabled
        state_text = "ATIVADO" if self.mouse_control_enabled else "DESATIVADO"
        self.status_label.config(text=f"Eye Tracking: {state_text} (Pressione F7)")
        if not self.mouse_control_enabled:
            self.currently_snapped_widget = None

    def update_loop(self):
        if self.mouse_control_enabled and self.tracker:
            gaze_data = self.tracker.get_screen_gaze()

            if gaze_data:
                gaze_x, gaze_y, _, _, _ = gaze_data
                min_dist_sq = float("inf")
                closest_widget = None

                for widget in self.focusable_widgets:
                    if not widget.winfo_exists():
                        continue
                    x, y, w, h = widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height()
                    center_x, center_y = x + w / 2, y + h / 2
                    dist_sq = (gaze_x - center_x) ** 2 + (gaze_y - center_y) ** 2

                    if dist_sq < min_dist_sq:
                        min_dist_sq = dist_sq
                        closest_widget = widget

                min_dist = min_dist_sq ** 0.5

                if closest_widget and min_dist <= SNAP_THRESHOLD_PIXELS:
                    if closest_widget != self.currently_snapped_widget:
                        target_x = closest_widget.winfo_rootx() + closest_widget.winfo_width() / 2
                        target_y = closest_widget.winfo_rooty() + closest_widget.winfo_height() / 2
                        pyautogui.moveTo(target_x, target_y, duration=0.1)
                        self.currently_snapped_widget = closest_widget
                else:
                    pyautogui.moveTo(gaze_x, gaze_y, duration=0.1)
                    self.currently_snapped_widget = None

                with self.shared_state["_lock"]:
                    click_request = self.shared_state.get("click_request", False)
                    if click_request:
                        self.shared_state["click_request"] = False

                if click_request and self.currently_snapped_widget:
                    print(f"CLIQUE ocular em: {self.currently_snapped_widget.cget('text')}")
                    pyautogui.click()

        self.after(50, self.update_loop)

    def quit_app(self):
        if self.tracker:
            self.tracker.stop()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
