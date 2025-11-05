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

    EAR_THRESHOLD = 0.2
    # BLINK_CLICK_DURATION foi movido para o main.py

    def __init__(self, camera_index: int = 0, shared_state: dict = None):
        super().__init__(daemon=True, name="EyeTrackerThread")
        self.camera_index = camera_index
        self.shared_state = shared_state or {}
        self.lock = self.shared_state.get("_lock", threading.Lock())
        self.running = False
        self.cap = None
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = None
        self.left_locked = False
        self.right_locked = False
        self.left_sphere_local_offset = None
        self.right_sphere_local_offset = None
        self.left_calibration_nose_scale = None
        self.right_calibration_nose_scale = None
        self.R_ref_nose = [None]
        self.base_radius = 20
        self.loaded_profile_name = None

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
        self.face_mesh = self.mp_face_mesh.FaceMesh(max_num_faces=1, refine_landmarks=True)
        if not self.cap or not self.cap.isOpened():
            self.cap = cv2.VideoCapture(self.camera_index)
        
        # Garante que a captura foi bem-sucedida
        if not self.cap.isOpened():
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

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb)
            gaze_is_valid = False

            # --- DADOS PADRÃO PARA O SHARED_STATE ---
            current_is_blinking = False

            if results.multi_face_landmarks:
                landmarks = results.multi_face_landmarks[0].landmark
                head_center, R_final, nose_points_3d = mc.compute_and_draw_coordinate_box(
                    frame, landmarks, mc.nose_indices, self.R_ref_nose
                )

                if self.left_locked and self.right_locked:
                    iris_left_3d = self._compute_iris_center(landmarks, self.LEFT_IRIS_INDEXES)
                    iris_right_3d = self._compute_iris_center(landmarks, self.RIGHT_IRIS_INDEXES)

                    current_nose_scale = mc.compute_scale(nose_points_3d)
                    scale_ratio_l = current_nose_scale / self.left_calibration_nose_scale
                    scale_ratio_r = current_nose_scale / self.right_calibration_nose_scale

                    sphere_world_l = head_center + R_final @ (self.left_sphere_local_offset * scale_ratio_l)
                    sphere_world_r = head_center + R_final @ (self.right_sphere_local_offset * scale_ratio_r)

                    left_dir = mc._normalize(iris_left_3d - sphere_world_l)
                    right_dir = mc._normalize(iris_right_3d - sphere_world_r)
                    combined_dir = mc._normalize((left_dir + right_dir) / 2.0)

                    mc.combined_gaze_directions.append(combined_dir)
                    avg_gaze_dir = mc._normalize(np.mean(mc.combined_gaze_directions, axis=0))

                    screen_x, screen_y, raw_yaw, raw_pitch = mc.convert_gaze_to_screen_coordinates(
                        avg_gaze_dir, mc.calibration_offset_yaw, mc.calibration_offset_pitch
                    )
                    last_valid_gaze = (screen_x, screen_y, raw_yaw, raw_pitch, 1.0)
                    gaze_is_valid = True

                # --- LÓGICA DE PISCADA SIMPLIFICADA (FASE 3) ---
                left_ear = self._compute_ear(landmarks, self.LEFT_EYE_OUTLINE_IDX)
                right_ear = self._compute_ear(landmarks, self.RIGHT_EYE_OUTLINE_IDX)
                avg_ear = (left_ear + right_ear) / 2.0
                current_is_blinking = avg_ear < self.EAR_THRESHOLD
                # --- FIM DA LÓGICA DE PISCADA ---

            # --- ATUALIZA O ESTADO COMPARTILHADO ---
            with self.lock:
                if gaze_is_valid:
                    self.shared_state["gaze"] = last_valid_gaze
                
                # Envia o estado da piscada continuamente
                self.shared_state["is_blinking"] = current_is_blinking

            time.sleep(0.001)

        self.stop()

    def start_debug_window(self, window_pos=None):
        """Abre a janela de debug (OpenCV) e permite calibrar. Fecha com 'q'."""
        if not self.cap or not self.cap.isOpened():
            self.cap = cv2.VideoCapture(self.camera_index)
        mc.w, mc.h = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.face_mesh = self.mp_face_mesh.FaceMesh(max_num_faces=1, refine_landmarks=True)

        print("[EyeTracker] Janela de debug aberta...")

        win_name = "Head/Eye Debug"
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        if window_pos and isinstance(window_pos, tuple) and len(window_pos) == 2:
            try:
                cv2.moveWindow(win_name, int(window_pos[0]), int(window_pos[1]))
            except Exception:
                pass

        while True:
            mc.update_orbit_from_keys()
            ret, frame = self.cap.read()
            if not ret:
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb)

            head_center, R_final, all_landmarks_3d, combined_dir = None, None, None, None
            sphere_world_l, sphere_world_r, iris_left_3d, iris_right_3d = None, None, None, None
            scaled_radius_l, scaled_radius_r = None, None

            if results.multi_face_landmarks:
                landmarks = results.multi_face_landmarks[0].landmark
                all_landmarks_3d = np.array([[lm.x * mc.w, lm.y * mc.h, lm.z * mc.w] for lm in landmarks])
                head_center, R_final, nose_points_3d = mc.compute_and_draw_coordinate_box(
                    frame, landmarks, mc.nose_indices, self.R_ref_nose
                )
                iris_left_3d = self._compute_iris_center(landmarks, self.LEFT_IRIS_INDEXES)
                iris_right_3d = self._compute_iris_center(landmarks, self.RIGHT_IRIS_INDEXES)

                if self.left_locked and self.right_locked:
                    current_nose_scale = mc.compute_scale(nose_points_3d)
                    scale_ratio_l = current_nose_scale / self.left_calibration_nose_scale
                    scale_ratio_r = current_nose_scale / self.right_calibration_nose_scale

                    sphere_world_l = head_center + R_final @ (self.left_sphere_local_offset * scale_ratio_l)
                    sphere_world_r = head_center + R_final @ (self.right_sphere_local_offset * scale_ratio_r)
                    scaled_radius_l = self.base_radius * scale_ratio_l
                    scaled_radius_r = self.base_radius * scale_ratio_r

                    left_dir = mc._normalize(iris_left_3d - sphere_world_l)
                    right_dir = mc._normalize(iris_right_3d - sphere_world_r)
                    combined_dir = mc._normalize((left_dir + right_dir) / 2.0)

            debug_img = mc.render_debug_view_orbit(
                mc.h, mc.w,
                head_center3d=head_center,
                sphere_world_l=sphere_world_l,
                scaled_radius_l=scaled_radius_l,
                sphere_world_r=sphere_world_r,
                scaled_radius_r=scaled_radius_r,
                iris3d_l=iris_left_3d,
                iris3d_r=iris_right_3d,
                left_locked=self.left_locked,
                right_locked=self.right_locked,
                landmarks3d=all_landmarks_3d,
                combined_dir=combined_dir,
                monitor_corners=mc.monitor_corners,
                monitor_center=mc.monitor_center_w,
                monitor_normal=mc.monitor_normal_w,
                gaze_markers_arg=mc.gaze_markers
            )

            cv2.imshow(win_name, debug_img)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break

            if results.multi_face_landmarks:
                if key == ord('c'):
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
                    print("[Calibração] Plano do monitor criado e esferas oculares travadas.")

                elif key == ord('s') and combined_dir is not None:
                    _, _, raw_yaw, raw_pitch = mc.convert_gaze_to_screen_coordinates(combined_dir, 0.0, 0.0)
                    mc.calibration_offset_yaw = -raw_yaw
                    mc.calibration_offset_pitch = -raw_pitch
                    print("[Calibração] Centro da tela calibrado.")

        cv2.destroyAllWindows()
        # Libera a câmera para que o thread 'run' possa usá-la
        if self.cap:
            self.cap.release()
            self.cap = None

    def save_calibration(self):
        """
        Coleta todos os dados de calibração e os retorna como um dicionário
        pronto para ser salvo como JSON (serialização recursiva).
        """
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
        """Carrega os dados de calibração de um dicionário."""
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
        try:
            if self.cap:
                self.cap.release()
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