# src/ui/calibrator_view.py
import tkinter as tk
import cv2
from PIL import Image, ImageTk

PREVIEW_SIZE = (320, 240)


class CalibratorFrame(tk.Frame):
    """
    A "Visão" (View) da tela de Calibração e Perfis.
    O 'controller' é a instância principal da classe App.
    """

    def __init__(self, parent, controller):
        self.controller = controller
        super().__init__(parent, bg="#222")

        # Estado do preview desta tela
        self._calib_cap = None
        self._calib_job = None

        # --- Construção da UI ---

        tk.Label(self, text="Selecione um Perfil ou Crie um Novo",
                 font=("Arial", 26, "bold"), bg="#222", fg="white").pack(pady=30)

        # --- Carregar Perfil ---
        profiles = self.controller.get_profile_list()  # Pede a lista ao controller
        if profiles:
            tk.Label(self, text="Carregar Perfil Existente:",
                     font=("Arial", 16), bg="#222", fg="white").pack(pady=(20, 5))

            # O controller (main.py) armazena a variável
            self.controller.profile_var = tk.StringVar(self)
            self.controller.profile_var.set(profiles[0])

            tk.OptionMenu(self, self.controller.profile_var, *profiles).pack(pady=10)

            tk.Button(self, text="Carregar Perfil", font=("Arial", 18),
                      command=self.controller.load_profile_and_start).pack(pady=20)

        tk.Label(self, text="Ou crie um novo perfil:",
                 font=("Arial", 16), bg="#222", fg="white").pack(pady=(40, 5))

        # --- Monitor ---
        tk.Label(self, text="Monitor para Calibração:",
                 font=("Arial", 14), bg="#222", fg="white").pack(pady=(10, 5))

        monitor_options = [f"Monitor {i} ({m.width}x{m.height})"
                           for i, m in enumerate(self.controller.available_monitors)]

        self.controller.monitor_var = tk.StringVar(self)
        self.controller.monitor_var.set(monitor_options[self.controller.selected_monitor_index])
        tk.OptionMenu(self, self.controller.monitor_var, *monitor_options).pack(pady=10)

        # --- Câmera ---
        tk.Label(self, text="Câmera para Calibração:",
                 font=("Arial", 14), bg="#222", fg="white").pack(pady=(10, 5))

        cam_labels = [c["label"] for c in self.controller._camera_list]
        self.controller.camera_var = tk.StringVar(self)

        default_label = next((c["label"] for c in self.controller._camera_list
                              if c["index"] == self.controller.default_camera_index),
                             (cam_labels[0] if cam_labels else "Câmera 0"))
        self.controller.camera_var.set(default_label)

        # Handler para trocar o preview
        def _on_calib_cam_change(newlabel):
            self.controller.camera_var.set(newlabel)
            cam = next((c for c in self.controller._camera_list if c["label"] == newlabel), None)
            if cam:
                self._start_calib_preview(cam["index"])

        tk.OptionMenu(self, self.controller.camera_var, *cam_labels,
                      command=_on_calib_cam_change).pack(pady=10)

        # --- Botão Criar ---
        tk.Button(self, text="Criar Novo Perfil e Calibrar",
                  font=("Arial", 18), command=self.controller.run_calibration).pack(pady=20)

        # --- Preview ---
        self.calib_preview = tk.Label(self, bg="#000")
        self.calib_preview.pack(pady=(6, 0))

        sel_label = self.controller.camera_var.get()
        cam = next((c for c in self.controller._camera_list if c["label"] == sel_label), None)
        if cam:
            self._start_calib_preview(cam["index"])

    # --- Lógica de Preview (agora dentro da View) ---

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
        # Usa o 'after' do controller (a raiz do app)
        self._calib_job = self.controller.after(33, self._calib_preview_loop)

    def _stop_calib_preview(self):
        if self._calib_job is not None:
            self.controller.after_cancel(self._calib_job)
            self._calib_job = None
        if self._calib_cap is not None:
            try:
                self._calib_cap.release()
            except Exception:
                pass
            self._calib_cap = None

    def on_destroy(self):
        """Método de limpeza chamado pelo controller antes de destruir."""
        self._stop_calib_preview()