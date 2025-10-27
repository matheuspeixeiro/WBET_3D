# src/ui/dashboard.py
import tkinter as tk
from PIL import Image, ImageTk


# --- FUNÇÃO AUXILIAR MOVIDA ---
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


# --- NOVA CLASSE DE UI DO DASHBOARD ---
class DashboardFrame(tk.Frame):
    """
    A "Visão" (View) do Dashboard.
    Contém todos os widgets da tela principal.
    O 'controller' é a instância principal da classe App.
    """

    def __init__(self, parent, controller):
        self.controller = controller
        self.cor_fundo = "#0b4073"  # azul-escuro do app

        super().__init__(parent, bg=self.cor_fundo)

        # --- TODO O CÓDIGO DE CONSTRUÇÃO DA UI VEM PARA CÁ ---

        # Header com perfil + monitor
        active_profile = "Nenhum (Calibração Volátil)"
        if self.controller.tracker and getattr(self.controller.tracker, "loaded_profile_name", None):
            active_profile = self.controller.tracker.loaded_profile_name
        mon = self.controller.get_active_monitor()

        header = tk.Frame(self, bg=self.cor_fundo)
        header.pack(fill="x", padx=20, pady=(10, 0))

        tk.Label(header,
                 text="Simple Eye Tracker",
                 font=("Poppins", 22, "bold"),
                 bg=self.cor_fundo, fg="white").pack(side="left")

        tk.Label(header,
                 text=f"Perfil: {active_profile}   |   Monitor: {self.controller.selected_monitor_index} ({mon.width}x{mon.height})",
                 font=("Poppins", 14), bg=self.cor_fundo, fg="white").pack(side="right")

        # Área central: grade 2x2 de tiles grandes
        grid_frame = tk.Frame(self, bg=self.cor_fundo)
        grid_frame.pack(expand=True, fill="both", padx=24, pady=24)

        for r in range(2):
            grid_frame.rowconfigure(r, weight=1, uniform="row")
        for c in range(2):
            grid_frame.columnconfigure(c, weight=1, uniform="col")

        ICON_NOTEPAD = "resources/images/notepad.png"
        ICON_SETTINGS = "resources/images/settings.png"

        # Tile (0,0) — Notepad (funcional)
        t00, surf00 = _criar_tile(
            grid_frame,
            image_path=ICON_NOTEPAD,
            # Chama o método do *controller* (main.py)
            command=self.controller.create_notepad_view
        )
        t00.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)

        # Tile (0,1) — Configurações (funcional)
        t01, surf01 = _criar_tile(
            grid_frame,
            image_path=ICON_SETTINGS,
            # Chama o método do *controller* (main.py)
            command=self.controller.abrir_configuracoes_view
        )
        t01.grid(row=0, column=1, sticky="nsew", padx=16, pady=16)

        # Tile (1,0) — Slot vazio (inert)
        t10, surf10 = _criar_tile(
            grid_frame,
            image_path=None,
            command=None
        )
        t10.grid(row=1, column=0, sticky="nsew", padx=16, pady=16)

        # Tile (1,1) — Slot vazio (inert)
        t11, surf11 = _criar_tile(
            grid_frame,
            image_path=None,
            command=None
        )
        t11.grid(row=1, column=1, sticky="nsew", padx=16, pady=16)

        # Guarda a lista de widgets que podem receber foco
        self._focusable_widgets = [surf00, surf01, surf10, surf11]

        # Barra inferior: status + dica F7
        bottom = tk.Frame(self, bg=self.cor_fundo)
        bottom.pack(fill="x", pady=(0, 16))

        # Status Eye Tracking
        self.status_label = tk.Label(
            bottom,
            text="Eye Tracking: DESATIVADO (Pressione F7)",
            font=("Poppins", 16, "bold"), bg=self.cor_fundo, fg="white"
        )
        self.status_label.pack(side="right", padx=20)

    # --- Métodos para o controller acessar ---

    def get_focusable_widgets(self):
        """Retorna a lista de widgets que o tracker pode focar."""
        return self._focusable_widgets

    def update_status_label(self, text):
        """Atualiza o texto do label de status (ativado/desativado)."""
        self.status_label.config(text=text)