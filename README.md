# 🧠 Gaze-Based-Computer-Control

Um sistema de **rastreamento ocular 3D de baixo custo** para controle de computador, utilizando uma webcam padrão e **MediaPipe**.

---

## 📘 Descrição do Projeto

O **Gaze-Based-Computer-Control** é um sistema de tecnologia assistiva que permite a interação com computadores **usando apenas o movimento dos olhos**.

Desenvolvido em **Python**, o sistema utiliza uma **webcam convencional** e algoritmos de visão computacional para rastrear o olhar do usuário e convertê-lo em comandos de controle — como a **movimentação precisa do cursor** e **seleção de itens**.

A motivação principal é oferecer uma alternativa **acessível e de baixo custo** para indivíduos com deficiências motoras severas (como **ELA - Esclerose Lateral Amiotrófica**), que mantêm controle dos olhos, mas não dos membros.

---

## ⚙️ Funcionalidades Principais

- 🎯 **Controle Preciso do Cursor:**  
  Mapeia o olhar do usuário para coordenadas (x, y) exatas na tela, permitindo o controle total do mouse.

- 📐 **Calibração 3D:**  
  Utiliza um modelo 3D da cabeça e um processo de calibração para encontrar a intersecção precisa do vetor do olhar com o plano do monitor.

- 🖥️ **Suporte a Múltiplos Monitores:**  
  Detecta e permite calibrar/operar o sistema em qualquer monitor conectado.

- 🧩 **Dashboard de Acessibilidade (Tkinter):**  
  Interface com botões grandes ("tiles") que atraem o foco do olhar — *snap-to-widget*.

- 👁️ **Comando de Clique por Piscada:**  
  Reconhece piscadas longas para simular cliques do mouse.

- 👤 **Gerenciamento de Perfis:**  
  Salva e carrega diferentes calibrações para múltiplos usuários ou condições de iluminação.

- 🧠 **Estabilização de Olhar:**  
  Implementa atraso e tolerância no movimento para evitar jitter, além de congelar o cursor após um clique.

---

## 🧮 Como Funciona: Rastreamento 3D

Diferente dos rastreadores 2D que apenas mapeiam a posição da pupila, este projeto implementa um **modelo 3D de rastreamento ocular**, garantindo maior precisão.

### Etapas Técnicas

1. **Detecção de Marcos Faciais (MediaPipe):**  
   São detectados 478 marcos (face + íris) em tempo real.

2. **Reconstrução 3D da Cabeça:**  
   Esses marcos são usados para estimar a pose 3D da cabeça, obtendo origem (centro do olho) e direção do olhar (vetor).

3. **Calibração do Monitor:**  
   Durante a calibração, o usuário olha para o centro da tela.  
   O sistema coleta vetores do olhar e calcula o **plano virtual do monitor** no espaço 3D.

4. **Intersecção em Tempo Real:**  
   A cada frame, o vetor do olhar 3D é intersectado com o plano do monitor.  
   O ponto de intersecção é convertido em coordenadas 2D (x, y) — e o cursor do mouse é movido até lá.

---

## 🧰 Tecnologias Utilizadas

- **Python 3.10+**
- **OpenCV** → Captura e processamento de vídeo.
- **MediaPipe** → Detecção de íris e marcos faciais.
- **NumPy / SciPy** → Cálculos de geometria 3D.
- **Tkinter** → Interface gráfica (dashboard, telas de calibração e seleção).
- **PyAutoGUI** → Controle do cursor e simulação de cliques.
- **ScreenInfo** → Detecção de múltiplos monitores.

---

## 🚀 Como Usar

### 1️⃣ Instalação

```bash
# Clone o repositório
git clone https://github.com/matheuspeixeiro/WBET_3D.git

# (Opcional) Crie e ative um ambiente virtual
python3 -m venv venv
source venv/bin/activate  # No Windows: venv\Scripts\activate

# Instale as dependências
pip install -r requirements.txt
```

### 2️⃣ Executando o Sistema
```bash
python src/main.py
```

### 3️⃣ Fluxo de Uso
🖥️ Tela Inicial

Selecione:

- Monitor onde o controle será ativo.

- Câmera (prévia ao vivo incluída).

👤 Gerenciador de Perfis

- Carregar Perfil: Usa calibrações salvas anteriormente.

- Novo Perfil: Permite uma nova calibração do olhar.

🎯 Calibração

- Siga as instruções exibidas.

- Olhe para pontos específicos e pressione as teclas indicadas (C, S, Q).

🧩 Dashboard Principal

- Pressione F7 para ativar o controle ocular.

- Olhe para os tiles (botões grandes) — o cursor “cola” automaticamente neles.

- Pisque para clicar.

- Pressione F7 novamente para desativar.

### 🙏 Agradecimentos

Este projeto foi fortemente inspirado e utiliza conceitos fundamentais do trabalho de Jason Orlosky em seu projeto [Webcam3DTracker](https://github.com/jasonorlosky/Webcam3DTracker).

Recomenda-se fortemente visitar seu repositório e canal no YouTube para entender a fundo os conceitos matemáticos por trás do rastreamento ocular 3D
