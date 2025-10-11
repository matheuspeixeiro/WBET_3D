# src/tracking/eye_tracker.py
# Versão 2: Implementação completa da calibração 3D e cálculo de gaze.

import threading
import time
import json
import math
import os
from datetime import datetime

import cv2
import numpy as np
import mediapipe as mp

# Importa o núcleo refatorado
from . import monitor_core as mc


class EyeTracker(threading.Thread):
    LEFT_IRIS_INDEXES = [474, 475, 476, 477]
    RIGHT_IRIS_INDEXES = [469, 470, 471, 472]

    def __init__(self, camera_index: int = 0, shared_state: dict = None):
        super().__init__(daemon=True, name="EyeTrackerThread")

        self.camera_index = camera_index
        self.shared_state = shared_state
        self.lock = shared_state.get("_lock", threading.Lock())

        self.running = False
        self.cap = None
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = None

        # --- Estado interno da calibração (espelhado do script original) ---
        self.left_locked = False
        self.right_locked = False
        self.left_sphere_local_offset = None
        self.right_sphere_local_offset = None
        self.left_calibration_nose_scale = None
        self.right_calibration_nose_scale = None
        self.R_ref_nose = [None]
        self.base_radius = 20

        # --- Estado de detecção de piscar ---
        self.blink_start_time = 0
        self.BLINK_DURATION_THRESHOLD = 2.5  # Segundos para um clique (ajustado de 3s para mais responsividade)

    def _compute_iris_center(self, landmarks, indexes):
        """Calcula o centro da íris a partir da média dos seus landmarks."""
        points = np.array([[landmarks[i].x * mc.w, landmarks[i].y * mc.h, landmarks[i].z * mc.w] for i in indexes])
        return np.mean(points, axis=0)

    def _compute_ear(self, landmarks, eye_points_idxs):
        """Calcula o Eye Aspect Ratio (EAR) para detecção de piscar."""
        try:
            eye_points = np.array([[landmarks[i].x * mc.w, landmarks[i].y * mc.h] for i in eye_points_idxs])
            A = np.linalg.norm(eye_points[1] - eye_points[5])
            B = np.linalg.norm(eye_points[2] - eye_points[4])
            C = np.linalg.norm(eye_points[0] - eye_points[3])
            ear = (A + B) / (2.0 * C)
            return ear
        except:
            return 0.4  # Valor padrão se landmarks não estiverem disponíveis

    def run(self):
        """Loop principal da thread: processa frames e atualiza o shared_state com dados de gaze."""
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=1, refine_landmarks=True, min_detection_confidence=0.5, min_tracking_confidence=0.5
        )
        if not self.cap or not self.cap.isOpened():
            self.cap = cv2.VideoCapture(self.camera_index)

        mc.w, mc.h = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        while self.running:
            ret, frame = self.cap.read()
            if not ret: break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb)

            if results.multi_face_landmarks:
                landmarks = results.multi_face_landmarks[0].landmark
                head_center, R_final, nose_points_3d = mc.compute_and_draw_coordinate_box(
                    frame, landmarks, mc.nose_indices, self.R_ref_nose
                )

                # --- Lógica de Detecção de Piscar Prolongado ---
                left_ear = self._compute_ear(landmarks, [362, 385, 387, 373, 390, 263])  # Indices para olho esquerdo
                right_ear = self._compute_ear(landmarks, [133, 160, 158, 144, 153, 33])  # Indices para olho direito
                avg_ear = (left_ear + right_ear) / 2.0

                if avg_ear < 0.2:  # Limiar para olho fechado
                    if self.blink_start_time == 0:
                        self.blink_start_time = time.time()
                    elif (time.time() - self.blink_start_time) > self.BLINK_DURATION_THRESHOLD:
                        with self.lock:
                            self.shared_state["click_request"] = True
                        self.blink_start_time = 0  # Reseta para evitar múltiplos cliques
                else:
                    self.blink_start_time = 0

                # --- Cálculo de Gaze 3D (APENAS SE CALIBRADO) ---
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

                    with self.lock:
                        self.shared_state['gaze'] = (screen_x, screen_y, raw_yaw, raw_pitch, 1.0)

            time.sleep(0.001)

        self.stop()

    def start_debug_window(self):
        """Abre a janela de debug bloqueante para calibração, com todas as funcionalidades."""
        if not self.cap or not self.cap.isOpened():
            self.cap = cv2.VideoCapture(self.camera_index)

        mc.w, mc.h = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.face_mesh = self.mp_face_mesh.FaceMesh(max_num_faces=1, refine_landmarks=True)

        print("[EyeTracker] Janela de debug aberta...")

        while True:
            mc.update_orbit_from_keys()
            ret, frame = self.cap.read()
            if not ret: break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb)

            # Zera variáveis para este quadro
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
                mc.h, mc.w, head_center3d=head_center, sphere_world_l=sphere_world_l,
                scaled_radius_l=scaled_radius_l, sphere_world_r=sphere_world_r, scaled_radius_r=scaled_radius_r,
                iris3d_l=iris_left_3d, iris3d_r=iris_right_3d, left_locked=self.left_locked,
                right_locked=self.right_locked,
                landmarks3d=all_landmarks_3d, combined_dir=combined_dir, monitor_corners=mc.monitor_corners,
                monitor_center=mc.monitor_center_w, monitor_normal=mc.monitor_normal_w, gaze_markers_arg=mc.gaze_markers
            )

            cv2.imshow("Head/Eye Debug", debug_img)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'): break

            if results.multi_face_landmarks:
                if key == ord('c'):
                    current_nose_scale = mc.compute_scale(nose_points_3d)
                    camera_dir_local = R_final.T @ np.array([0, 0, 1])

                    self.left_sphere_local_offset = R_final.T @ (
                                iris_left_3d - head_center) + self.base_radius * camera_dir_local
                    self.right_sphere_local_offset = R_final.T @ (
                                iris_right_3d - head_center) + self.base_radius * camera_dir_local
                    self.left_calibration_nose_scale = self.right_calibration_nose_scale = current_nose_scale
                    self.left_locked = self.right_locked = True

                    gaze_dir_hint = mc._normalize(
                        iris_left_3d - (head_center + R_final @ self.left_sphere_local_offset))

                    mc.monitor_corners, mc.monitor_center_w, mc.monitor_normal_w, mc.units_per_cm = mc.create_monitor_plane(
                        head_center, R_final, landmarks, mc.w, mc.h, gaze_dir=gaze_dir_hint
                    )
                    print("[Calibração] Plano do monitor criado e esferas oculares travadas.")

                elif key == ord('s') and combined_dir is not None:
                    _, _, raw_yaw, raw_pitch = mc.convert_gaze_to_screen_coordinates(combined_dir, 0.0, 0.0)
                    mc.calibration_offset_yaw = -raw_yaw
                    mc.calibration_offset_pitch = -raw_pitch
                    print(f"[Calibração] Centro da tela calibrado.")

        cv2.destroyAllWindows()

    def start(self):
        """Inicia a thread de rastreamento em segundo plano."""
        self.running = True
        super().start()

    def stop(self):
        """Para a thread de forma segura."""
        self.running = False
        if self.cap: self.cap.release()
        if self.face_mesh: self.face_mesh.close()

    def get_screen_gaze(self):
        with self.lock:
            return self.shared_state.get('gaze')