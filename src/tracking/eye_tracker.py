# src/tracking/eye_tracker.py
# VERSÃO ATUALIZADA: Suporta calibração não-bloqueante (controlada pelo main.py)

import threading
import time
from datetime import datetime
import cv2
import numpy as np
import mediapipe as mp
from . import monitor_core as mc


class EyeTracker(threading.Thread):
    # --- CONSTANTES ---
    LEFT_IRIS_INDEXES = [474, 475, 476, 477]
    RIGHT_IRIS_INDEXES = [469, 470, 471, 472]
    LEFT_EYE_OUTLINE_IDX = [362, 385, 387, 263, 390, 373]
    RIGHT_EYE_OUTLINE_IDX = [133, 160, 158, 33, 153, 144]
    #EAR_THRESHOLD = 0.30

    def __init__(self, camera_index: int = 0, shared_state: dict = None):
        super().__init__(daemon=True, name="EyeTrackerThread")
        self.camera_index = camera_index
        self.shared_state = shared_state or {}
        self.lock = self.shared_state.get("_lock", threading.RLock())
        self.running = False
        self.cap = None
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = None
        
        # Estado de calibração
        self.left_locked = False
        self.right_locked = False
        self.left_sphere_local_offset = None
        self.right_sphere_local_offset = None
        self.left_calibration_nose_scale = None
        self.right_calibration_nose_scale = None
        self.R_ref_nose = [None]
        self.base_radius = 20
        self.loaded_profile_name = None

        # --- NOVOS: Variáveis de Calibração de EAR ---
        self.ear_threshold_left = 0.30  # Valor padrão inicial
        self.ear_threshold_right = 0.30 # Valor padrão inicial
        
        # --- NOVOS: Variáveis de Calibração de EAR ---
        self.ear_threshold_left = 0.30
        self.ear_threshold_right = 0.30
        
        # Histórico para média (últimos 30 frames)
        self.ear_history_left = []
        self.ear_history_right = []
        
        # --- FLAGS DE CONTROLE SEPARADOS ---
        self._calibrating_blink = False  # E2
        self._calibrating_boost = False  # E3
        
        # --- VALORES CAPTURADOS ---
        # Repouso (E1)
        self._avg_open_left = 0.35
        self._avg_open_right = 0.35
        # Piscada Dupla (E2)
        self._min_blink_left = 1.0
        self._min_blink_right = 1.0
        # Boost/Wink (E3)
        self._min_boost_right = 1.0

        # --- NOVOS: Flags de controle de calibração ---
        self._trigger_calib_step_c = False
        self._trigger_calib_step_s = False
        self._latest_frame = None
        self._face_detected_in_frame = False

    def _compute_iris_center(self, landmarks, indexes):
        points = np.array([[landmarks[i].x * mc.w, landmarks[i].y * mc.h, landmarks[i].z * mc.w] for i in indexes])
        return np.mean(points, axis=0)

    def _compute_ear(self, landmarks, eye_points_idxs):
        try:
            eye_points = np.array([[landmarks[i].x * mc.w, landmarks[i].y * mc.h] for i in eye_points_idxs])
            A = np.linalg.norm(eye_points[1] - eye_points[5])
            B = np.linalg.norm(eye_points[2] - eye_points[4])
            C = np.linalg.norm(eye_points[0] - eye_points[3])
            return (A + B) / (2.0 * C)
        except Exception:
            return 0.4

    def run(self):
        """Loop principal da thread: processa frames, calibra e rastreia."""
        self.face_mesh = self.mp_face_mesh.FaceMesh(max_num_faces=1, refine_landmarks=True)
        self.cap = cv2.VideoCapture(self.camera_index)
        
        if not self.cap or not self.cap.isOpened():
            print(f"ERRO: Não foi possível abrir a câmera índice {self.camera_index}")
            self.running = False
            return
            
        mc.w, mc.h = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.running = True
        last_valid_gaze = None

        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.005)
                continue

            # Salva o frame para o preview da UI
            with self.lock:
                self._latest_frame = frame.copy()

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb)
            
            gaze_is_valid = False
            current_is_blinking = False
            current_is_boosting = False

            if results.multi_face_landmarks:
                self._face_detected_in_frame = True
                landmarks = results.multi_face_landmarks[0].landmark

                # --- 1. CALCULO DE EAR ---
                left_ear = self._compute_ear(landmarks, self.LEFT_EYE_OUTLINE_IDX)
                right_ear = self._compute_ear(landmarks, self.RIGHT_EYE_OUTLINE_IDX)

                # Atualiza buffer (para passo E1 - Repouso)
                self.ear_history_left.append(left_ear)
                self.ear_history_right.append(right_ear)
                if len(self.ear_history_left) > 30: self.ear_history_left.pop(0)
                if len(self.ear_history_right) > 30: self.ear_history_right.pop(0)

                # --- 2. CAPTURA DE DADOS DE CALIBRAÇÃO ---
                
                # Passo E2: Capturando Piscada Dupla (Clique)
                if self._calibrating_blink:
                    if left_ear < self._min_blink_left: self._min_blink_left = left_ear
                    if right_ear < self._min_blink_right: self._min_blink_right = right_ear
                
                # Passo E3: Capturando Boost (Wink Direito)
                if self._calibrating_boost:
                    if right_ear < self._min_boost_right: self._min_boost_right = right_ear
                
                # --- LÓGICA DE CALIBRAÇÃO (MOVIDA PARA CÁ) ---
                # Esta lógica é necessária para os passos 'C' e 'S'
                head_center, R_final, nose_points_3d = mc.compute_and_draw_coordinate_box(
                    frame, landmarks, mc.nose_indices, self.R_ref_nose
                )
                iris_left_3d = self._compute_iris_center(landmarks, self.LEFT_IRIS_INDEXES)
                iris_right_3d = self._compute_iris_center(landmarks, self.RIGHT_IRIS_INDEXES)

                # --- DISPARADOR PARA O PASSO 'C' ---
                if self._trigger_calib_step_c:
                    current_nose_scale = mc.compute_scale(nose_points_3d)
                    camera_dir_local = R_final.T @ np.array([0, 0, 1])
                    self.left_sphere_local_offset = R_final.T @ (iris_left_3d - head_center) + self.base_radius * camera_dir_local
                    self.right_sphere_local_offset = R_final.T @ (iris_right_3d - head_center) + self.base_radius * camera_dir_local
                    self.left_calibration_nose_scale = self.right_calibration_nose_scale = current_nose_scale
                    self.left_locked = self.right_locked = True
                    gaze_dir_hint = mc._normalize(iris_left_3d - (head_center + R_final @ self.left_sphere_local_offset))
                    mc.monitor_corners, mc.monitor_center_w, mc.monitor_normal_w, mc.units_per_cm = mc.create_monitor_plane(
                        head_center, R_final, landmarks, mc.w, mc.h, gaze_dir=gaze_dir_hint
                    )
                    print("[Calibração] Passo C: Plano do monitor criado e esferas oculares travadas.")
                    self._trigger_calib_step_c = False # Reseta o flag

                # A lógica de gaze só roda *depois* da calibração 'C'
                if self.left_locked and self.right_locked:
                    current_nose_scale = mc.compute_scale(nose_points_3d)
                    scale_ratio_l = current_nose_scale / self.left_calibration_nose_scale
                    scale_ratio_r = current_nose_scale / self.right_calibration_nose_scale
                    sphere_world_l = head_center + R_final @ (self.left_sphere_local_offset * scale_ratio_l)
                    sphere_world_r = head_center + R_final @ (self.right_sphere_local_offset * scale_ratio_r)
                    left_dir = mc._normalize(iris_left_3d - sphere_world_l)
                    right_dir = mc._normalize(iris_right_3d - sphere_world_r)
                    combined_dir = mc._normalize((left_dir + right_dir) / 2.0)

                    # --- DISPARADOR PARA O PASSO 'S' ---
                    if self._trigger_calib_step_s:
                        _, _, raw_yaw, raw_pitch = mc.convert_gaze_to_screen_coordinates(combined_dir, 0.0, 0.0)
                        mc.calibration_offset_yaw = -raw_yaw
                        mc.calibration_offset_pitch = -raw_pitch
                        print("[Calibração] Passo S: Centro da tela calibrado.")
                        self._trigger_calib_step_s = False # Reseta o flag
                    
                    # --- LÓGICA NORMAL DE GAZE ---
                    mc.combined_gaze_directions.append(combined_dir)
                    avg_gaze_dir = mc._normalize(np.mean(mc.combined_gaze_directions, axis=0))
                    screen_x, screen_y, raw_yaw, raw_pitch = mc.convert_gaze_to_screen_coordinates(
                        avg_gaze_dir, mc.calibration_offset_yaw, mc.calibration_offset_pitch
                    )
                    last_valid_gaze = (screen_x, screen_y, raw_yaw, raw_pitch, 1.0)
                    gaze_is_valid = True

                # --- 3. DETECÇÃO COM LIMIARES DINÂMICOS ---
                is_left_blinking = left_ear < self.ear_threshold_left
                is_right_blinking = right_ear < self.ear_threshold_right
                
                current_is_blinking = is_left_blinking and is_right_blinking
                # Boost: Direita fechada E Esquerda aberta
                current_is_boosting = is_right_blinking and (not is_left_blinking)
            
            else:
                self._face_detected_in_frame = False

            # --- ATUALIZA O ESTADO COMPARTILHADO ---
            with self.lock:
                if gaze_is_valid:
                    self.shared_state["gaze"] = last_valid_gaze
                self.shared_state["is_blinking"] = current_is_blinking
                self.shared_state["is_boosting"] = current_is_boosting

            time.sleep(0.001)

        self.stop() # Limpa o self.cap

    # --- MÉTODO REMOVIDO ---
    # start_debug_window(self, window_pos=None):
    #     (Este método foi removido e sua lógica integrada ao run())

    # --- NOVOS MÉTODOS DE CALIBRAÇÃO DE EAR ---

    def calibrate_step_open(self):
        """E1: Registra o estado de repouso (olhos abertos)."""
        if self.ear_history_left:
            self._avg_open_left = sum(self.ear_history_left) / len(self.ear_history_left)
            self._avg_open_right = sum(self.ear_history_right) / len(self.ear_history_right)
            print(f"[EAR] Repouso -> L:{self._avg_open_left:.3f} R:{self._avg_open_right:.3f}")
            return True
        return False

    def calibrate_ears_step_open(self):
        """Passo 1: Captura a média dos olhos abertos (repouso)."""
        if self.ear_history_left:
            self._captured_avg_open_left = sum(self.ear_history_left) / len(self.ear_history_left)
            self._captured_avg_open_right = sum(self.ear_history_right) / len(self.ear_history_right)
            print(f"[EAR Calib] Aberto Médio -> L: {self._captured_avg_open_left:.3f}, R: {self._captured_avg_open_right:.3f}")
            return True
        return False

    def start_blink_capture(self):
        """Inicia captura E2 (Piscada Dupla)."""
        self._min_blink_left = 1.0
        self._min_blink_right = 1.0
        self._calibrating_blink = True

    def stop_blink_capture(self):
        self._calibrating_blink = False
        print(f"[EAR] Blink Mínimos -> L:{self._min_blink_left:.3f} R:{self._min_blink_right:.3f}")

    def start_boost_capture(self):
        """Inicia captura E3 (Piscada Direita / Boost)."""
        self._min_boost_right = 1.0
        self._calibrating_boost = True

    def stop_boost_capture(self):
        self._calibrating_boost = False
        print(f"[EAR] Boost Mínimo -> R:{self._min_boost_right:.3f}")
        self._finalize_thresholds()

    def _finalize_thresholds(self):
        """Calcula os limiares finais combinando as 3 etapas."""
        
        # Limiar Esquerdo: Média entre Aberto e Fechado (Blink)
        thresh_l = (self._avg_open_left + self._min_blink_left) / 2
        
        # Limiar Direito: Precisamos ser cuidadosos aqui.
        # O olho direito fecha tanto no Blink quanto no Boost.
        # Pegamos o "pior caso" (o valor mais baixo registrado em qualquer uma das ações)
        # para garantir que o limiar detecte ambos.
        min_right_total = min(self._min_blink_right, self._min_boost_right)
        thresh_r = (self._avg_open_right + min_right_total) / 2
        
        # Travas de segurança (Clamps)
        # Impede que o limiar fique impossível (ex: < 0.12) ou muito sensível (ex: > 0.40)
        self.ear_threshold_left = max(0.12, min(0.40, thresh_l))
        self.ear_threshold_right = max(0.12, min(0.40, thresh_r))
        
        print(f"[EAR] FINAIS -> L:{self.ear_threshold_left:.3f} R:{self.ear_threshold_right:.3f}")

    def start_ear_action_capture(self):
        """Inicia a gravação dos valores mínimos (usuário vai piscar/winkar)."""
        self._captured_min_left = 1.0
        self._captured_min_right = 1.0
        self._calibrating_ears = True

    def stop_ear_action_capture(self):
        """Finaliza gravação e calcula os limiares finais."""
        self._calibrating_ears = False
        
        # Cálculo: Média entre o estado aberto e o estado mais fechado registrado
        # Adicionamos um pequeno 'padding' de segurança (ex: 0.02) para não ficar sensível demais
        margin = 0.0
        
        thresh_l = (self._captured_avg_open_left + self._captured_min_left) / 2 - margin
        thresh_r = (self._captured_avg_open_right + self._captured_min_right) / 2 - margin
        
        # Proteção contra valores absurdos
        self.ear_threshold_left = max(0.10, min(0.45, thresh_l))
        self.ear_threshold_right = max(0.10, min(0.45, thresh_r))
        
        print(f"[EAR Calib] Minimos -> L: {self._captured_min_left:.3f}, R: {self._captured_min_right:.3f}")
        print(f"[EAR Calib] NOVOS LIMIARES -> L: {self.ear_threshold_left:.3f}, R: {self.ear_threshold_right:.3f}")

    # --- NOVOS MÉTODOS DE CONTROLE ---
    def get_latest_frame_and_status(self):
        """Chamado pela UI (via main.py) para o preview da calibração."""
        with self.lock:
            frame = self._latest_frame.copy() if self._latest_frame is not None else None
            face_detected = self._face_detected_in_frame
        
        if frame is not None:
             # Converte BGR (do OpenCV) para RGB (do Tkinter/PIL)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return frame, face_detected

    def trigger_calibration_step(self, step: str):
        """Chamado pelo main.py para disparar a calibração 'C' ou 'S'."""
        if step == 'C':
            self._trigger_calib_step_c = True
        elif step == 'S':
            self._trigger_calib_step_s = True
    # --- FIM DOS NOVOS MÉTODOS ---

    def save_calibration(self):
        # (Este método permanece o mesmo - com a correção do ndarray)
        if not (self.left_locked and self.right_locked):
            print("AVISO: Tentando salvar calibração sem estar calibrado.")
            return None

        def to_list_safe(item):
            if isinstance(item, np.ndarray):
                return item.tolist()
            elif isinstance(item, (list, tuple)):
                return [to_list_safe(i) for i in item]
            elif isinstance(item, dict):
                return {k: to_list_safe(v) for k, v in item.items()}
            elif isinstance(item, (np.floating, np.integer)):
                return item.item()
            return item

        calib_data = {
            "calibration_date": datetime.utcnow().isoformat() + "Z",
            "camera_index": self.camera_index,
            "calibration_offsets": {
                "yaw": mc.calibration_offset_yaw,
                "pitch": mc.calibration_offset_pitch
            },
            "ear_thresholds": {
                "left": self.ear_threshold_left,
                "right": self.ear_threshold_right
            },
            "monitor_plane": to_list_safe({
                "corners": mc.monitor_corners,
                "center": mc.monitor_center_w,
                "normal": mc.monitor_normal_w,
                "units_per_cm": mc.units_per_cm
            }),
            "left_sphere_local_offset": to_list_safe(self.left_sphere_local_offset),
            "right_sphere_local_offset": to_list_safe(self.right_sphere_local_offset),
            "left_calibration_nose_scale": self.left_calibration_nose_scale,
            "right_calibration_nose_scale": self.right_calibration_nose_scale,
        }
        return calib_data

    def load_calibration(self, calib_data: dict, profile_name: str = None):
        # (Este método permanece o mesmo)
        try:
            offsets = calib_data["calibration_offsets"]
            mc.calibration_offset_yaw = float(offsets["yaw"])
            mc.calibration_offset_pitch = float(offsets["pitch"])
            plane = calib_data["monitor_plane"]
            mc.monitor_corners = np.array(plane["corners"], dtype=float)
            mc.monitor_center_w = np.array(plane["center"], dtype=float)
            mc.monitor_normal_w = np.array(plane["normal"], dtype=float)
            mc.units_per_cm = float(plane["units_per_cm"])
            self.left_sphere_local_offset = np.array(calib_data["left_sphere_local_offset"], dtype=float)
            self.right_sphere_local_offset = np.array(calib_data["right_sphere_local_offset"], dtype=float)
            self.left_calibration_nose_scale = float(calib_data["left_calibration_nose_scale"])
            self.right_calibration_nose_scale = float(calib_data["right_calibration_nose_scale"])
            self.left_locked = self.right_locked = True
            if profile_name:
                self.loaded_profile_name = profile_name
            print("Dados de calibração carregados com sucesso no tracker.")

            ear_data = calib_data.get("ear_thresholds", {})
            self.ear_threshold_left = float(ear_data.get("left", 0.30))
            self.ear_threshold_right = float(ear_data.get("right", 0.30))

            return True
        except Exception as e:
            print(f"ERRO ao carregar dados de calibração: {e}")
            return False

    def start(self):
        if self.is_alive():
            return
        self.running = True
        super().start()

    def stop(self):
        self.running = False
        time.sleep(0.05) # Dá tempo para a thread 'run' terminar
        try:
            if self.cap:
                self.cap.release()
                self.cap = None
        except Exception:
            pass
        try:
            if self.face_mesh:
                self.face_mesh.close()
        except Exception:
            pass

    def get_screen_gaze(self):
        with self.lock:
            return self.shared_state.get("gaze")