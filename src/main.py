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
from tracking import calibration
from tkinter import simpledialog

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
        # Inicia a aplicação em tela cheia, como solicitado
        self.attributes('-fullscreen', True)

        self.tracker = None
        self.shared_state = {"_lock": threading.Lock()}
        self.mouse_control_enabled = False

        self.focusable_widgets = []
        self.currently_snapped_widget = None

        self.create_calibrator_view()

    # --- TELA DE CALIBRAÇÃO E PERFIS (ATUALIZADA) ---
    def create_calibrator_view(self):
        for widget in self.winfo_children():
            widget.destroy()

        self.configure(bg="#222")
        frame = tk.Frame(self, bg="#222")
        frame.pack(expand=True)

        tk.Label(frame, text="Selecione um Perfil ou Crie um Novo", font=("Arial", 26, "bold"), bg="#222",
                 fg="white").pack(pady=30)

        # --- Listagem de Perfis Existentes ---
        profiles = calibration.list_profiles()
        if profiles:
            tk.Label(frame, text="Carregar Perfil Existente:", font=("Arial", 16), bg="#222", fg="white").pack(
                pady=(20, 5))
            self.profile_var = tk.StringVar(self)
            self.profile_var.set(profiles[0])
            tk.OptionMenu(frame, self.profile_var, *profiles).pack(pady=10)
            tk.Button(frame, text="Carregar Perfil", font=("Arial", 18), command=self.load_profile_and_start).pack(
                pady=20)

        # --- Opção para Criar Novo Perfil ---
        tk.Label(frame, text="Ou crie um novo perfil:", font=("Arial", 16), bg="#222", fg="white").pack(
            pady=(40, 5))
        self.camera_var = tk.StringVar(self)
        self.camera_var.set("Câmera 0")
        tk.OptionMenu(frame, self.camera_var, "Câmera 0", "Câmera 1").pack(pady=10)
        tk.Button(frame, text="Criar Novo Perfil e Calibrar", font=("Arial", 18),
                  command=self.run_calibration).pack(pady=20)

    def load_profile_and_start(self):
        """Carrega um perfil de calibração e inicia o tracker."""
        profile_name = self.profile_var.get()
        calib_data = calibration.load_profile(profile_name)

        if not calib_data:
            messagebox.showerror("Erro", f"Não foi possível carregar o perfil '{profile_name}'.")
            return

        camera_index = calib_data.get("camera_index", 0)

        self.tracker = EyeTracker(camera_index=camera_index, shared_state=self.shared_state)
        self.tracker.load_calibration(calib_data)  # Carrega os dados no tracker
        self.tracker.start()  # Inicia o tracker em segundo plano

        self.create_dashboard()

    def run_calibration(self):
        """Inicia o EyeTracker, abre a janela de calibração e salva um novo perfil."""
        profile_name = simpledialog.askstring("Novo Perfil",
                                              "Digite um nome para o novo perfil (ex: Matheus - Casa, Mesa de Jantar):")
        if not profile_name:
            return  # Usuário cancelou

        camera_index = int(self.camera_var.get().split()[-1])

        messagebox.showinfo(
            "Instruções de Calibração",
            "1. Olhe para o centro da tela e pressione 'C'.\n"
            "2. Olhe para o centro novamente e pressione 'S'.\n"
            "3. Pressione 'Q' para fechar a janela quando terminar."
        )

        self.tracker = EyeTracker(camera_index=camera_index, shared_state=self.shared_state)
        self.tracker.start_debug_window()  # Executa a calibração

        # Após a calibração, salva os dados no novo perfil
        calib_data = self.tracker.save_calibration()  # Obtém os dados
        calibration.save_profile(profile_name, calib_data)  # Salva no arquivo

        self.tracker.start()  # Inicia o tracker em segundo plano
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
