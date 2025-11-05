# src/ui/notepad_view.py
import tkinter as tk


class NotepadFrame(tk.Frame):
    """
    A "Visão" (View) da tela do Bloco de Notas.
    O 'controller' é a instância principal da classe App.
    """

    def __init__(self, parent, controller):
        self.controller = controller
        self.cor_fundo = "#0b4073"
        super().__init__(parent, bg=self.cor_fundo)

        # Lista de widgets que esta tela expõe ao tracker
        self._focusable_widgets = []

        # --- Layout Principal ---
        main_frame = tk.Frame(self, bg=self.cor_fundo)
        main_frame.pack(fill="both", expand=True)

        # 1. Barra Lateral (Esquerda)
        sidebar_frame = tk.Frame(main_frame, bg="#083057", width=100)
        sidebar_frame.pack(side="left", fill="y", padx=(10, 0), pady=10)
        sidebar_frame.pack_propagate(False)

        # 2. Área de Conteúdo (Direita)
        content_frame = tk.Frame(main_frame, bg=self.cor_fundo)
        content_frame.pack(side="left", fill="both", expand=True, padx=20, pady=10)

        # --- 1. Povoar Barra Lateral ---
        self._build_notepad_sidebar(sidebar_frame)

        # --- 2. Povoar Área de Conteúdo ---
        # 2a. Header (Botões Salvar/Novo)
        header_frame = tk.Frame(content_frame, bg=self.cor_fundo)
        header_frame.pack(fill="x", pady=(0, 10))

        btn_novo = tk.Button(header_frame, text="Novo Documento", font=("Arial", 16),
                             command=self.controller._handle_new_document)
        btn_novo.pack(side="left", padx=10)

        btn_salvar = tk.Button(header_frame, text="Salvar", font=("Arial", 16),
                               command=self.controller._handle_save_document)
        btn_salvar.pack(side="right", padx=10)

        self._focusable_widgets.extend([btn_novo, btn_salvar])

        # 2c. Teclado Virtual (VEM PRIMEIRO)
        keyboard_frame = tk.Frame(content_frame, bg=self.cor_fundo, pady=10)
        # Garanta que "side='bottom'" esteja aqui
        keyboard_frame.pack(fill="x", side="bottom")
        self._build_virtual_keyboard(keyboard_frame)

        # 2b. Área de Texto (VEM DEPOIS)
        text_frame = tk.Frame(content_frame, bg="white", borderwidth=2, relief="solid")
        # Este "expand=True" agora só vai preencher o espaço que sobrou
        text_frame.pack(fill="both", expand=True)

        # --- Bloco Faltante Adicionado ---
        # O controller armazena a referência ao widget
        self.controller.notepad_text_widget = tk.Text(text_frame, wrap="word", font=("Arial", 20),
                                                   undo=True, bg="white", fg="black",
                                                   insertbackground="black", relief="flat")
        self.controller.notepad_text_widget.pack(fill="both", expand=True, padx=10, pady=10)
        self._focusable_widgets.append(self.controller.notepad_text_widget)

        # Preenche com o conteúdo "não salvo"
        if self.controller.notepad_last_save_content and self.controller.notepad_is_dirty:
             self.controller.notepad_text_widget.insert("1.0", self.controller.notepad_last_save_content)
        else:
            self.controller.notepad_last_save_content = ""

        # Rastreia alterações
        self.controller.notepad_text_widget.bind("<<Modified>>",
                                                self.controller._on_notepad_modified)
        # --- Fim do Bloco Faltante ---

        # --- 3. Barra de Status (Inferior) ---
        bottom_bar = tk.Frame(self, bg=self.cor_fundo)
        bottom_bar.pack(fill="x", pady=(0, 16))

        self.status_label = tk.Label(
            bottom_bar,
            text="Eye Tracking: DESATIVADO (Pressione F7)",
            font=("Poppins", 16, "bold"), bg=self.cor_fundo, fg="white"
        )
        self.status_label.pack(side="right", padx=20)

        # Foca o widget de texto por padrão
        self.controller.notepad_text_widget.focus_set()

    # --- Métodos de Construção ---

    def _build_notepad_sidebar(self, parent_frame):
        """Cria os botões da barra lateral para o notepad."""

        # Botão CASA (Voltar ao Dashboard)
        btn_home = tk.Button(parent_frame, image=self.controller.icon_home, bg="#0b4073",
                             activebackground="#115a9e", relief="flat", borderwidth=0,
                             command=self.controller.create_dashboard)
        btn_home.pack(pady=(300, 20), fill="x", padx=10)

        # Botão NOTEPAD (Ativo)
        btn_notepad = tk.Button(parent_frame, image=self.controller.icon_notepad, bg="#1E88E5",
                                relief="flat", borderwidth=0, state="disabled")
        btn_notepad.pack(pady=10, fill="x", padx=10)

        self._focusable_widgets.append(btn_home)

    def _build_virtual_keyboard(self, parent_frame):
        """Cria e exibe o teclado virtual."""

        # --- CORREÇÃO DE ESTILO ---
        # Estilo "Claro" que o macOS respeita.
        # Removemos width/height para que o layout seja responsivo.
        key_style = {"font": ("Arial", 16),
                     "bg": "#EEEEEE",  # Fundo cinza-claro
                     "fg": "black",  # Texto preto (VISÍVEL)
                     "activebackground": "#CCCCCC",
                     "activeforeground": "black",
                     "relief": "flat",
                     "borderwidth": 1,
                     "padx": 10, "pady": 10}  # Aumenta o padding interno

        # Estilo para teclas especiais (Shift, Enter, etc.)
        special_key_style = key_style.copy()
        special_key_style["bg"] = "#CCCCCC"  # Cinza um pouco mais escuro
        # --- FIM DA CORREÇÃO DE ESTILO ---

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
            # --- CORREÇÃO DE LAYOUT ---
            # Faz a 'linha' (row_frame) preencher a largura
            row_frame.pack(fill="x")

            for key_char in row:

                # Escolhe o estilo (especial ou normal)
                is_special = len(key_char) > 1 or not key_char.isalnum()
                style_to_use = special_key_style if is_special else key_style

                btn = tk.Button(row_frame, text=key_char, **style_to_use)

                # --- Lógica de Comando ---
                cmd = lambda char=key_char: self._on_key_press(char)
                btn.configure(command=cmd)

                # --- Lógica de Tamanho REMOVIDA ---
                # (Não precisamos mais de width=60 ou width=8)

                # --- CORREÇÃO DE LAYOUT ---
                # Faz os 'botões' preencherem a 'linha' e se expandirem
                btn.pack(side="left", fill="x", expand=True, padx=2, pady=2)
                self._focusable_widgets.append(btn)

                # Guarda referência dos botões de estado
                if key_char == 'Shift':
                    self.controller.shift_btn_ref = btn
                if key_char == 'Caps':
                    self.controller.caps_btn_ref = btn

    def _on_key_press(self, key_char):
        """Lida com cliques do teclado, atualizando o estado do controller."""

        # Pega o widget de texto do controller
        widget = self.controller.notepad_text_widget
        if not widget:
            return

        # 1. Teclas de Controle
        if key_char == 'Backspace':
            widget.delete(tk.INSERT + "-1c", tk.INSERT)
        elif key_char == 'Enter':
            widget.insert(tk.INSERT, '\n')
        elif key_char == 'Tab':
            widget.insert(tk.INSERT, '\t')
        elif key_char == 'Space':
            widget.insert(tk.INSERT, ' ')

        # 2. Teclas Modificadoras (Atualiza o estado no controller)
        elif key_char == 'Shift':
            self.controller.sticky_shift_active = not self.controller.sticky_shift_active
            new_color = "#1E88E5" if self.controller.sticky_shift_active else "#CCCCCC" # Corrigido para estilo claro
            if hasattr(self.controller, "shift_btn_ref"):
                self.controller.shift_btn_ref.configure(bg=new_color)

        elif key_char == 'Caps':
            self.controller.caps_lock_active = not self.controller.caps_lock_active
            new_color = "#1E88E5" if self.controller.caps_lock_active else "#CCCCCC" # Corrigido para estilo claro
            if hasattr(self.controller, "caps_btn_ref"):
                self.controller.caps_btn_ref.configure(bg=new_color)

        # 3. Teclas de Caractere (Lê o estado do controller)
        else:
            char_to_insert = key_char
            is_letter = key_char.isalpha() and len(key_char) == 1

            is_upper = self.controller.caps_lock_active ^ self.controller.sticky_shift_active

            if is_letter:
                char_to_insert = key_char.upper() if is_upper else key_char.lower()

            widget.insert(tk.INSERT, char_to_insert)

            # Atualiza o estado no controller
            if self.controller.sticky_shift_active:
                self.controller.sticky_shift_active = False
                if hasattr(self.controller, "shift_btn_ref"):
                    self.controller.shift_btn_ref.configure(bg="#CCCCCC") # Corrigido para estilo claro

        widget.focus_set()
        widget.event_generate("<<Modified>>")

    # --- Métodos para o controller acessar ---

    def get_focusable_widgets(self):
        return self._focusable_widgets

    def update_status_label(self, text):
        self.status_label.config(text=text)

    def on_destroy(self):
        """Método de limpeza chamado pelo controller."""
        # Esta view não precisa de limpeza especial (como parar um preview)
        pass