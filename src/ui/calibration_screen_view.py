# src/ui/calibration_screen_view.py
import tkinter as tk
import cv2
from PIL import Image, ImageTk

# Tamanho do preview da câmera no canto
PREVIEW_WIDTH = 320
PREVIEW_HEIGHT = 240

class CalibrationScreenFrame(tk.Frame):
    """
    A "Visão" (View) da tela de Calibração ATIVA (ponto verde).
    Esta tela é controlada pelo MOUSE por um assistente.
    """
    def __init__(self, parent, controller):
        self.controller = controller
        # Fundo preto para focar no ponto verde
        super().__init__(parent, bg="black")
        
        # Estado do preview desta tela
        self._calib_job = None
        self.current_frame = None
        # Calcula um tamanho de preview responsivo (aprox 15-20% da tela)
        self.preview_size = (
            int(PREVIEW_WIDTH * self.controller.get_active_monitor().width / 1920),
            int(PREVIEW_HEIGHT * self.controller.get_active_monitor().height / 1080)
        )
        
        # --- 1. O Ponto Verde Central (Prioridade 1) ---
        self.center_dot_label = tk.Label(self, text="+", 
                                         font=("Arial", 30, "bold"), 
                                         fg="#00FF00", bg="black")
        # .place() o coloca no centro exato do monitor
        self.center_dot_label.place(relx=0.5, rely=0.5, anchor="center")

        # --- 2. Preview da Câmera (Canto Inferior Esquerdo) ---
        self.camera_feed_label = tk.Label(self, bg="black", 
                                          width=self.preview_size[0], 
                                          height=self.preview_size[1],
                                          borderwidth=2, relief="solid")
        # .place() o ancora no canto inferior esquerdo
        self.camera_feed_label.place(relx=0.01, rely=0.99, anchor="sw")

        # --- 3. Ações e Instruções (Canto Inferior Direito) ---
        action_frame = tk.Frame(self, bg="#222", borderwidth=2, relief="raised")
        # .place() o ancora no canto inferior direito
        action_frame.place(relx=0.99, rely=0.99, anchor="se")

        tk.Label(action_frame, text=f"Calibrando Perfil:",
                 font=("Arial", 14), bg="#222", fg="white").pack(pady=(10,0), padx=10)
        
        # Pega o nome do perfil do controller
        profile_name = getattr(self.controller, "current_profile_name", "N/A")
        tk.Label(action_frame, text=f"{profile_name}",
                 font=("Arial", 16, "bold"), bg="#222", fg="#00FF00").pack(pady=(0,10), padx=10)

        self.instruction_label = tk.Label(action_frame, text="Carregando câmera...",
                                          font=("Arial", 14), bg="#222", fg="white",
                                          wraplength=300, justify="left")
        self.instruction_label.pack(pady=10, padx=10)

        # Botão de Ação Principal (controlado pelo main.py)
        self.action_button = tk.Button(action_frame, text="...", 
                                      font=("Arial", 18, "bold"), 
                                      command=self.controller.on_calib_button_click,
                                      state="disabled")
        self.action_button.pack(pady=10, padx=10, fill="x", ipady=10)
        
        # Botão de Cancelar
        cancel_button = tk.Button(action_frame, text="Cancelar", 
                                  font=("Arial", 16), 
                                  command=self.controller.cancel_calibration)
        cancel_button.pack(pady=10, padx=10, fill="x", ipady=5)

        # Inicia o loop de atualização da câmera
        self._update_camera_feed()

    def _update_camera_feed(self):
        # Chama os métodos do controller (main.py)
        frame, face_detected = self.controller.get_calib_frame_data()
        
        # O controller é responsável por atualizar os botões/texto
        self.controller.update_calib_ui(face_detected, self.instruction_label, self.action_button)

        # Atualiza preview
        if frame is not None:
            try:
                img = Image.fromarray(frame).resize(self.preview_size)
                imgtk = ImageTk.PhotoImage(img)
                self.camera_feed_label.configure(image=imgtk)
                self.camera_feed_label.image = imgtk
            except Exception as e:
                print(f"Erro ao atualizar feed de calibração: {e}")

        self._calib_job = self.controller.after(33, self._update_camera_feed)

    def on_destroy(self):
        """Método de limpeza chamado pelo controller antes de destruir."""
        if self._calib_job:
            try:
                self.controller.after_cancel(self._calib_job)
            except:
                pass
            self._calib_job = None